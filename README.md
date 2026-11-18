# Clips GTA 6 — publication automatique sur TikTok et YouTube Shorts (0 €)

Toutes les heures, GitHub :
1. récupère les clips Twitch les plus vus des chaînes de `channels.txt`, filtrés sur GTA VI ;
2. les passe en vertical 9:16 (fond flouté) avec des sous-titres français façon TikTok et le crédit du streamer ;
3. met les clips seuls dans la file **YouTube Shorts**, et des **compilations de plus d’une minute** (avec une accroche au début) dans la file **TikTok**, via Buffer, qui publie aux horaires que tu as choisis.

Tu n'as rien à faire tourner chez toi, et tout est gratuit.

## Mise en place (une seule fois, ~15 min)

### 1. Buffer (publication)
- Crée un compte gratuit sur buffer.com.
- Connecte ton compte **TikTok** et ta chaîne **YouTube**. Pour TikTok, Buffer demande un compte **Professionnel** : dans l'appli TikTok → Paramètres → Compte → « Passer à un compte professionnel » (gratuit).
- Dans Buffer → *Settings* → *Posting schedule* : choisis les horaires de publication, par exemple 12h, 18h et 21h.
- Récupère ta clé sur **publish.buffer.com/settings/api** (« Get API Key »).

### 2. Twitch (récupération des clips)
- Sur dev.twitch.tv/console → *Register Your Application* : OAuth Redirect `http://localhost`, catégorie « Application Integration », client « Confidential ».
- Note le **Client ID** et crée un **Client Secret** (« New Secret »).

### 3. GitHub (le moteur)
- Crée un dépôt **public** et mets-y tous ces fichiers. Sur un dépôt public, GitHub Actions est gratuit et illimité.
- Remplis `channels.txt` : une chaîne Twitch autorisée par ligne.
- Dans *Settings* → *Secrets and variables* → *Actions* → *New repository secret*, ajoute ces 3 secrets :
  - `TWITCH_CLIENT_ID`
  - `TWITCH_CLIENT_SECRET`
  - `BUFFER_API_KEY`
- Dans *Settings* → *Actions* → *General* → *Workflow permissions*, choisis **Read and write permissions**.
- Pour un premier essai : onglet *Actions* → « Clips GTA 6 » → *Run workflow*.

## Limites de la version gratuite
- Buffer gratuit : **10 posts en attente par compte**. Le script remplit la file, Buffer publie, et la file se remplit de nouveau au passage suivant.
- Clé API Buffer gratuite : 3 000 requêtes par mois. Avec un passage par heure, ça suffit.
- Les vidéos sont hébergées sur la branche `media` du dépôt, puis supprimées automatiquement au bout de 10 jours.

## Réglages possibles
À ajouter dans le bloc `env:` de l'étape « Fabriquer et publier les clips » :

| Réglage | Défaut | Rôle |
|---|---|---|
| `MAX_CLIPS_PER_RUN` | 6 | Nombre max de clips seuls par passage (YouTube Shorts) |
| `MAX_COMPILATIONS_PER_RUN` | 2 | Nombre max de compilations par passage (TikTok) |
| `COMP_MIN_SECONDS` | 65 | Durée minimum d'une compilation TikTok (rémunération > 1 min) |
| `COMP_MAX_CLIPS` | 5 | Nombre max de clips par compilation |
| `MIN_VIEWS` | 5 | Vues Twitch minimum pour garder un clip |
| `LOOKBACK_HOURS` | 24 | Ancienneté max des clips |
| `ONLY_GTA6` | 1 | Mettre 0 pour prendre aussi les clips hors GTA |

## À savoir
- Garde une preuve écrite de l'accord de chaque streamer.
- Pour suivre ce qui est publié : onglet *Actions* (journal de chaque passage) et file d'attente Buffer.
