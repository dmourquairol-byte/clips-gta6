"""
Clips GTA 6 — récupère les meilleurs clips Twitch des chaînes autorisées,
les passe en vertical 9:16 avec sous-titres FR, et les publie sur TikTok et YouTube Shorts via Buffer.

Variables d'environnement (secrets GitHub) :
  BUFFER_API_KEY (obligatoire) ; TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET (facultatifs, API officielle)
Réglages optionnels :
  LOOKBACK_HOURS (défaut 24), MAX_CLIPS_PER_RUN (défaut 6), MIN_VIEWS (défaut 5),
  ONLY_GTA6 (défaut 1), WHISPER_MODEL (défaut "small")
"""
import json
import os
import subprocess
import sys
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CHANNELS_FILE = ROOT / "channels.txt"
STATE_FILE = ROOT / "posted.json"
GAME_NAME = "Grand Theft Auto VI"

LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
MAX_CLIPS = int(os.getenv("MAX_CLIPS_PER_RUN", "6"))          # clips seuls pour YouTube Shorts
MAX_COMPILATIONS = int(os.getenv("MAX_COMPILATIONS_PER_RUN", "2"))  # compilations pour TikTok
COMP_MIN_SECONDS = float(os.getenv("COMP_MIN_SECONDS", "65"))  # TikTok ne rémunère que > 1 min
COMP_MAX_CLIPS = int(os.getenv("COMP_MAX_CLIPS", "5"))
MIN_VIEWS = int(os.getenv("MIN_VIEWS", "5"))
ONLY_GTA6 = os.getenv("ONLY_GTA6", "1") == "1"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")

HASHTAGS = "#GTA6 #GTAVI #GTA #gaming #twitch #fyp #pourtoi"


# ---------------------------------------------------------------- Twitch
def twitch_token(cid, secret):
    r = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def helix(path, params, cid, token):
    r = requests.get(f"https://api.twitch.tv/helix/{path}", params=params, timeout=30,
                     headers={"Client-Id": cid, "Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()["data"]


def read_channels():
    out = []
    for line in CHANNELS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().rstrip("/")
        if line:
            out.append(line.split("/")[-1].lower())
    return out


def is_gta6(category):
    c = (category or "").lower()
    return "grand theft auto vi" in c or "gta vi" in c or "gta 6" in c or "grand theft auto 6" in c


def fetch_clips_public(logins, game_cache):
    """Sans compte Twitch : lit les pages publiques « Clips » des chaînes (via yt-dlp)."""
    import yt_dlp
    rng = "24hr" if LOOKBACK_HOURS <= 24 else ("7d" if LOOKBACK_HOURS <= 168 else "30d")
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp()
    flat = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True,
                             "skip_download": True, "playlistend": 20})
    full = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True})
    clips = []
    for login in logins:
        try:
            info = flat.extract_info(f"https://www.twitch.tv/{login}/clips?filter=clips&range={rng}", download=False)
        except Exception as e:
            print(f"⚠️  {login} : impossible de lire les clips ({e})")
            continue
        for e in info.get("entries") or []:
            if not e or not e.get("url") or (e.get("view_count") or 0) < MIN_VIEWS:
                continue
            if e.get("timestamp") and e["timestamp"] < since:
                continue
            cid = str(e.get("id") or e["url"].rstrip("/").split("/")[-1])
            if ONLY_GTA6:
                if cid not in game_cache:
                    try:
                        cats = full.extract_info(e["url"], download=False).get("categories") or []
                        game_cache[cid] = cats[0] if cats else ""
                    except Exception:
                        game_cache[cid] = ""
                if not is_gta6(game_cache[cid]):
                    continue
            clips.append({"id": cid, "url": e["url"], "title": e.get("title") or "",
                          "broadcaster_name": login, "view_count": e.get("view_count") or 0,
                          "duration": float(e.get("duration") or 30)})
    clips.sort(key=lambda c: c["view_count"], reverse=True)
    return clips


def fetch_clips(cid, token, logins):
    game_id = None
    if ONLY_GTA6:
        games = helix("games", {"name": GAME_NAME}, cid, token)
        game_id = games[0]["id"] if games else None
        if not game_id:
            print("⚠️  Jeu GTA VI introuvable sur Twitch, pas de filtre par jeu.")
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = []
    for i in range(0, len(logins), 100):
        for u in helix("users", [("login", l) for l in logins[i:i + 100]], cid, token):
            data = helix("clips", {"broadcaster_id": u["id"], "started_at": since, "first": 50}, cid, token)
            for c in data:
                if game_id and c.get("game_id") != game_id:
                    continue
                if c["view_count"] < MIN_VIEWS:
                    continue
                clips.append(c)
    clips.sort(key=lambda c: c["view_count"], reverse=True)
    return clips


# ---------------------------------------------------------------- Vidéo
def download(clip, workdir):
    out = workdir / f"{clip['id']}.mp4"
    subprocess.run(["yt-dlp", "-q", "-f", "best[ext=mp4]/best", "-o", str(out), clip["url"]], check=True)
    return out


def transcribe(video):
    """Renvoie une liste de mots [(début, fin, texte)]."""
    from faster_whisper import WhisperModel
    global _model
    if "_model" not in globals():
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = _model.transcribe(str(video), language="fr", word_timestamps=True, vad_filter=True)
    words = []
    for s in segments:
        for w in (s.words or []):
            if w.word.strip():
                words.append((w.start, w.end, w.word.strip()))
    return words


def ts(t):
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def esc(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")")


def build_ass(words, credit, duration, path, hook=None):
    """Sous-titres façon TikTok : 3 mots max à l'écran, gros, centrés, mot courant en jaune."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,DejaVu Sans,82,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,3,5,60,60,0,1
Style: Credit,DejaVu Sans,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,2,8,40,40,170,1
Style: Hook,DejaVu Sans,74,&H0000FFFF,&H0000FFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,3,8,50,50,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{ts(0)},{ts(duration)},Credit,,0,0,0,,{esc(credit)}"]
    if hook:  # accroche en haut pendant les 3 premières secondes (compilations TikTok)
        lines.append(f"Dialogue: 2,{ts(0)},{ts(min(3.5, duration))},Hook,,0,0,0,,{esc(hook.upper())}")
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 3 or w[2][-1:] in ".?!,":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    for chunk in chunks:
        for i, (start, end, _) in enumerate(chunk):
            nxt = chunk[i + 1][0] if i + 1 < len(chunk) else end
            text = " ".join(
                ("{\\c&H00FFFF&}" + esc(t.upper()) + "{\\c&HFFFFFF&}") if j == i else esc(t.upper())
                for j, (_, _, t) in enumerate(chunk))
            lines.append(f"Dialogue: 1,{ts(start)},{ts(max(nxt, start + 0.05))},Sub,,0,0,0,,{{\\pos(540,1340)}}{text}")
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def render(src, ass, out):
    """1080x1920 : fond flouté + vidéo pleine largeur au centre + sous-titres incrustés."""
    vf = ("[0:v]split[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:2,eq=brightness=-0.15[bg];"
          "[b]scale=1080:-2[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
          f"subtitles='{ass.as_posix()}'[v]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", vf,
                    "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)


def duration_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


# ---------------------------------------------------------------- Hébergement de la vidéo
MEDIA_DIR = ROOT / "media_branch"   # branche « media » du dépôt, ouverte par le workflow
MEDIA_KEEP_DAYS = 10


def host_video(video):
    """Pousse la vidéo sur la branche « media » et renvoie un lien public direct (raw.githubusercontent.com)."""
    repo = os.environ["GITHUB_REPOSITORY"]
    dest = MEDIA_DIR / video.name
    shutil.copy(video, dest)
    git = lambda *a: subprocess.run(["git", "-C", str(MEDIA_DIR), *a], check=True)
    git("add", dest.name)
    git("commit", "-q", "-m", f"clip {dest.name}")
    git("push", "-q", "origin", "HEAD:media")
    time.sleep(15)  # laisse le temps au CDN de GitHub
    return f"https://raw.githubusercontent.com/{repo}/media/{dest.name}"


def clean_old_media():
    """Supprime les vidéos de plus de MEDIA_KEEP_DAYS jours (déjà publiées)."""
    if not MEDIA_DIR.exists():
        return
    limit = time.time() - MEDIA_KEEP_DAYS * 86400
    old = []
    for f in MEDIA_DIR.glob("*.mp4"):
        r = subprocess.run(["git", "-C", str(MEDIA_DIR), "log", "-1", "--format=%ct", "--", f.name],
                           capture_output=True, text=True)
        if r.stdout.strip() and int(r.stdout.strip()) < limit:
            old.append(f.name)
    if old:
        git = lambda *a: subprocess.run(["git", "-C", str(MEDIA_DIR), *a], check=True)
        git("rm", "-q", *old)
        git("commit", "-q", "-m", "nettoyage")
        git("push", "-q", "origin", "HEAD:media")


# ---------------------------------------------------------------- Publication (Buffer)
BUFFER_URL = "https://api.buffer.com"


class BufferFull(Exception):
    pass


def buffer_gql(key, query, variables=None):
    r = requests.post(BUFFER_URL, json={"query": query, "variables": variables or {}}, timeout=60,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def buffer_channels(key):
    """Trouve tout seul les comptes TikTok et YouTube connectés à Buffer."""
    orgs = buffer_gql(key, "query { account { organizations { id } } }")["account"]["organizations"]
    chans = []
    for o in orgs:
        q = "query($id: OrganizationId!) { channels(input: {organizationId: $id}) { id service name } }"
        chans += buffer_gql(key, q, {"id": o["id"]})["channels"]
    return [c for c in chans if c["service"].lower() in ("tiktok", "youtube")]


CREATE_POST = """
mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id } }
    ... on MutationError { message }
  }
}"""




def buffer_post(key, channel, video_url, text, yt_title=None):
    inp = {
        "channelId": channel["id"],
        "schedulingType": "automatic",
        "mode": "addToQueue",          # Buffer publie selon les créneaux de ta file d'attente
        "assets": [{"video": {"url": video_url}}],
        "text": text,
    }
    if yt_title:
        inp["metadata"] = {"youtube": {"title": yt_title[:100], "categoryId": "20"}}
    res = buffer_gql(key, CREATE_POST, {"input": inp})["createPost"]
    if "message" in res:
        msg = res["message"]
        if any(w in msg.lower() for w in ("limit", "queue", "full", "upgrade")):
            raise BufferFull(msg)
        raise RuntimeError(msg)
    return res["post"]["id"]


def post_to(key, channels, url, text, yt_title=None):
    """Publie sur une liste de comptes. Renvoie False si toutes les files sont pleines."""
    full = 0
    for ch in channels:
        try:
            buffer_post(key, ch, url, text, yt_title)
            print(f"   ✓ ajouté à la file {ch['service']} ({ch['name']})")
        except BufferFull as e:
            full += 1
            print(f"   ⏸ file {ch['service']} pleine : {e}")
    return full < len(channels)


# ---------------------------------------------------------------- Compilations TikTok
def plan_compilations(clips):
    """Regroupe les clips en vidéos de plus d'une minute : d'abord par streamer, puis un best-of avec les restes."""
    groups = {}
    for c in clips:  # clips déjà triés par vues
        groups.setdefault(c["broadcaster_name"], []).append(c)
    comps, leftovers = [], []
    for name, cs in groups.items():
        cur = []
        for c in cs:
            cur.append(c)
            if sum(x["duration"] for x in cur) >= COMP_MIN_SECONDS or len(cur) == COMP_MAX_CLIPS:
                if sum(x["duration"] for x in cur) >= COMP_MIN_SECONDS:
                    comps.append(cur)
                    cur = []
                else:
                    leftovers += cur
                    cur = []
        leftovers += cur
    leftovers.sort(key=lambda c: c["view_count"], reverse=True)
    cur = []
    for c in leftovers:
        cur.append(c)
        if sum(x["duration"] for x in cur) >= COMP_MIN_SECONDS:
            comps.append(cur)
            cur = []
    comps.sort(key=lambda g: sum(c["view_count"] for c in g), reverse=True)
    return comps


def concat(videos, out):
    n = len(videos)
    inputs = sum((["-i", str(v)] for v in videos), [])
    fc = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(out)], check=True)


def comp_texts(group):
    names = list(dict.fromkeys(c["broadcaster_name"] for c in group))
    if len(names) == 1:
        hook = f"{names[0]} sur GTA 6 😂"
        title = f"Les meilleurs moments de {names[0]} sur GTA 6 😂🔥"
    else:
        hook = "Les moments les plus fous de GTA 6 🔥"
        title = "Les moments les plus fous de GTA 6 😂🔥"
    credits = " · ".join(f"twitch.tv/{n}" for n in names)
    return hook, f"{title}\n🎥 {credits}\n{HASHTAGS}"


# ---------------------------------------------------------------- Main
def load_state():
    if not STATE_FILE.exists():
        return {"shorts": [], "tiktok": []}
    data = json.loads(STATE_FILE.read_text())
    if isinstance(data, list):  # ancien format
        return {"shorts": data, "tiktok": list(data)}
    return data


def save_state(state):
    out = {k: (v[-2000:] if isinstance(v, list) else dict(list(v.items())[-3000:])) for k, v in state.items()}
    STATE_FILE.write_text(json.dumps(out, indent=0, ensure_ascii=False))


def main():
    env = {k: os.getenv(k) for k in ["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "BUFFER_API_KEY"]}
    missing = [k for k in ["BUFFER_API_KEY"] if not env[k]]
    if missing:
        sys.exit(f"Secrets manquants : {', '.join(missing)}")
    key = env["BUFFER_API_KEY"]

    logins = read_channels()
    if not logins:
        sys.exit("channels.txt est vide : ajoute les chaînes autorisées.")

    channels = buffer_channels(key)
    yt = [c for c in channels if c["service"].lower() == "youtube"]
    tt = [c for c in channels if c["service"].lower() == "tiktok"]
    if not channels:
        sys.exit("Aucun compte TikTok ou YouTube connecté à Buffer.")
    print(f"Comptes : {len(yt)} YouTube, {len(tt)} TikTok")

    clean_old_media()
    state = load_state()
    if env["TWITCH_CLIENT_ID"] and env["TWITCH_CLIENT_SECRET"]:  # option : API officielle Twitch
        token = twitch_token(env["TWITCH_CLIENT_ID"], env["TWITCH_CLIENT_SECRET"])
        clips = fetch_clips(env["TWITCH_CLIENT_ID"], token, logins)
    else:                                                          # par défaut : sans compte Twitch
        clips = fetch_clips_public(logins, state.setdefault("games", {}))
    print(f"{len(clips)} clip(s) GTA 6 trouvés sur les dernières {LOOKBACK_HOURS} h.")
    save_state(state)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rendered = {}

        def get_rendered(c, hook=None):
            k = (c["id"], hook)
            if k not in rendered:
                if c["id"] not in rendered.get("raw", {}):
                    rendered.setdefault("raw", {})[c["id"]] = download(c, tmp)
                    rendered.setdefault("words", {})[c["id"]] = transcribe(rendered["raw"][c["id"]])
                raw = rendered["raw"][c["id"]]
                suffix = "_hook" if hook else ""
                ass = tmp / f"{c['id']}{suffix}.ass"
                build_ass(rendered["words"][c["id"]], f"twitch.tv/{c['broadcaster_name']}", duration_of(raw), ass, hook)
                out = tmp / f"{c['broadcaster_name']}_{c['id']}{suffix}.mp4"
                render(raw, ass, out)
                rendered[k] = out
            return rendered[k]

        # 1) YouTube Shorts : clips seuls
        if yt:
            todo = [c for c in clips if c["id"] not in state["shorts"]][:MAX_CLIPS]
            print(f"\n=== Shorts : {len(todo)} clip(s) ===")
            for c in todo:
                try:
                    print(f"→ {c['broadcaster_name']} · {c['title']} ({c['view_count']} vues)")
                    url = host_video(get_rendered(c))
                    title = c["title"].strip() or "Moment GTA 6"
                    ok = post_to(key, yt, url, f"{title}\n\n🎥 twitch.tv/{c['broadcaster_name']}\n{HASHTAGS} #Shorts",
                                 yt_title=f"{title} #GTA6 #Shorts")
                    if not ok:
                        print("File YouTube pleine : on reprendra au prochain passage.")
                        break
                    state["shorts"].append(c["id"])
                    save_state(state)
                except Exception as e:
                    print(f"   ✗ échec : {e}")

        # 2) TikTok : compilations de plus d'une minute
        if tt:
            pending = [c for c in clips if c["id"] not in state["tiktok"]]
            comps = plan_compilations(pending)[:MAX_COMPILATIONS]
            print(f"\n=== TikTok : {len(comps)} compilation(s) ({len(pending)} clip(s) en attente) ===")
            for group in comps:
                try:
                    hook, text = comp_texts(group)
                    print(f"→ {hook} : {len(group)} clips, {sum(c['duration'] for c in group):.0f} s")
                    parts = [get_rendered(c, hook if i == 0 else None) for i, c in enumerate(group)]
                    out = tmp / f"compil_{group[0]['id']}.mp4"
                    concat(parts, out)
                    if not post_to(key, tt, host_video(out), text):
                        print("File TikTok pleine : on reprendra au prochain passage.")
                        break
                    state["tiktok"] += [c["id"] for c in group]
                    save_state(state)
                except Exception as e:
                    print(f"   ✗ échec : {e}")


if __name__ == "__main__":
    main()
