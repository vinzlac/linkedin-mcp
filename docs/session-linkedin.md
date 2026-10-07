# Session LinkedIn pour le scraping (runbook)

Comment créer, vérifier, propager et renouveler le **fichier de session Playwright** qui
permet à `scrape_feed`, `scrape_post`, `repost_post` et aux outils invitations/messagerie
de parler à linkedin.com sans redemander de mot de passe.

> **Il n'y a pas de script à écrire** : contrairement à NotebookLM (où il a fallu un projet
> à part, `~/workspace/notebooklm-login`, parce que `notebooklm-sdk` déclare Playwright en
> peerDependency *optionnelle*), le mécanisme est intégré à ce repo depuis l'origine.

| | NotebookLM | LinkedIn (ce repo) |
|---|---|---|
| Outil de login | projet séparé `notebooklm-login` (`npm run login:chrome`) | `just session` / outil MCP `create_scrape_session` |
| Fichier produit | `~/.notebooklm/session.json` | `linkedin_session.json` (chemin par OS, voir plus bas) |
| Consommation | collé à la main dans la credential n8n « Session JSON » | lu directement via `LINKEDIN_SESSION_PATH` |
| Sur le cluster | — (n8n porte la credential) | session portée par le profil du Chromium dédié de gpu-node (ADR-005) ; Sealed Secret `linkedin-mcp-session` = amorçage de secours seulement |

## Ce que contient le fichier

Un **storage state Playwright** : les cookies du domaine linkedin.com (dont le cookie de
session) et le stockage local associé. C'est l'équivalent d'un navigateur déjà connecté.

Conséquence directe : **ce fichier vaut un accès complet au compte LinkedIn**. Voir
[Sécurité](#sécurité).

## Créer la session (local)

Trois portes d'entrée, même résultat :

```bash
just session                          # raccourci
uv run python create_session.py       # équivalent direct
```

ou, depuis un client MCP (Claude Desktop…), l'outil **`create_scrape_session`**.

Déroulé :

1. Une fenêtre **Chromium / Chrome for Testing** s'ouvre sur `https://www.linkedin.com/login`.
2. Tu te connectes **à la main** (email, mot de passe, 2FA). Le mot de passe n'est ni
   demandé ni stocké par le serveur MCP.
3. Tu attends que **le feed s'affiche** — c'est ce que le script détecte.
   Timeout : **5 minutes**.
4. La session est écrite sur disque et le fichier passe en **`chmod 600`**.

C'est la **seule** étape qui ouvre une fenêtre visible : le scraping quotidien tourne
headless (`LINKEDIN_HEADLESS=true` par défaut).

### Où atterrit le fichier

Chemin par défaut, hors du repo, résolu par `linkedin_mcp/config/settings.py` :

| OS | Chemin |
|---|---|
| macOS | `~/Library/Application Support/linkedin-mcp/linkedin_session.json` |
| Linux | `~/.local/state/linkedin-mcp/linkedin_session.json` |
| Windows | `%APPDATA%\linkedin-mcp\linkedin_session.json` |

Surchargeable via `LINKEDIN_SESSION_PATH` (chemin absolu) dans `.env` ou dans l'`env` du
client MCP.

## Vérifier

```bash
just test-feed 3                      # ou :
uv run python test_scrape_feeds.py 3 -
```

Si des posts remontent, la session est bonne. Sinon, voir
[Renouveler](#renouveler-la-session).

Le navigateur headless reste volontairement ouvert entre deux scrapes : utilise l'outil
**`close_scrape_browser`** pour le fermer proprement.

## Amorcer un profil vierge sur k3s (secours uniquement)

Depuis ADR-005 (2026-10-07), en k3s la session vit dans le **profil du Chromium dédié** de gpu-node,
et se crée et se renouvelle **là**, pas sur le Mac : voir [Renouveler la session](#renouveler-la-session).
Cette section ne sert qu'à **amorcer un profil vierge** (nouveau nœud, profil supprimé) : le pod injecte
alors le fichier de session du Sealed Secret, et seulement si le profil n'a pas de `li_at` valide.

**Attention** : une session créée ou utilisée sur le Mac puis scellée sera très probablement révoquée par
LinkedIn en quelques minutes. Après un amorçage, refaire aussitôt la procédure de
[Renouveler la session](#renouveler-la-session), **en commençant par vider les données du site**
(étape 4 : le profil amorcé porte les cookies d'appareil du Mac).

### Secours — Sealed Secret

```bash
export KUBECONFIG=~/.kube/config-k3s
./scripts/seal-secrets.sh
```

Le script lit le chemin de session résolu par `settings.py`, crée le Secret
`linkedin-mcp-session` (clé `linkedin_session.json`) dans le namespace `linkedin-mcp`,
le scelle avec `kubeseal` et écrit `kubernetes/linkedin-mcp-session.sealed.yaml`.
Il scelle au passage les tokens OAuth et les credentials OAuth.

Ensuite : commit + push du `.sealed.yaml`, Argo CD applique, puis redémarrer le
déploiement pour que le pod relise le fichier :

```bash
kubectl -n linkedin-mcp rollout restart deploy/linkedin-mcp
```

Côté pod, le secret est monté en lecture seule sur `/secrets/session` et
`LINKEDIN_SESSION_PATH` pointe sur `/secrets/session/linkedin_session.json`
(cf. `kubernetes/deployment.yaml`).

Prérequis : `kubectl` + `kubeseal` (`brew install kubeseal`).

### Outils MCP de transfert (développement local uniquement)

Pour passer une session d'une instance locale à une autre :

- **`get_scrape_session_json`** — renvoie le JSON brut de la session locale (valide le JSON
  avant de le rendre) ;
- **`set_scrape_session_json`** — écrit ce JSON dans la session de l'instance visée.

Ne fonctionne **pas en k3s** : le fichier de session y est monté en lecture seule depuis le Sealed
Secret, et le profil du Chromium dédié fait foi. Réservé au développement local ; pour k3s, suivre
[Renouveler la session](#renouveler-la-session).

## Renouveler la session

Les cookies LinkedIn expirent, et LinkedIn peut invalider une session (changement de mot de
passe, déconnexion globale, détection d'anomalie).

**Symptômes** : `scrape_feed` renvoie 0 post alors que le serveur démarre bien ; redirection
vers la page de login dans les traces ; outils invitations/messagerie vides.

**Procédure (k3s, depuis le 2026-10-07)** : se connecter **dans le Chromium dédié de gpu-node**, jamais
sur le Mac. Une session créée sur un navigateur puis utilisée depuis un autre est révoquée par LinkedIn
en quelques minutes (incident du 2026-10-07, ADR-005).

1. Tunnel, laissé ouvert pendant toute la procédure :
   `ssh -N -L 9243:127.0.0.1:9243 vinz@192.168.1.154` (port CDP interne de l'instance).
2. Ouvrir la page de connexion dans un **nouvel** onglet du Chromium dédié, par l'API CDP, depuis le Mac :

   ```bash
   curl -s -X PUT "http://localhost:9243/json/new?https://www.linkedin.com/login" \
     | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])"
   ```

   Noter l'identifiant affiché (`<ID>`). Ne **jamais** naviguer un onglet existant : le premier onglet
   (`pages[0]`, souvent `about:blank`) est l'onglet de travail du pod.
3. Ouvrir dans Chrome sur le Mac le DevTools **servi par le Chromium distant** (même version que lui) :
   `http://localhost:9243/devtools/inspector.html?ws=localhost:9243/devtools/page/<ID>`
   puis afficher la page avec **Cmd-Shift-M** (ou l'icône écran/téléphone de la barre DevTools) : la vue
   est en direct et transmet clavier et souris.

   Constaté le 2026-10-07 : depuis `chrome://inspect`, le bouton *Open tab with url* n'ouvrait rien, et le
   lien `devtools://devtools/bundled/…` du Chrome du Mac coupait la WebSocket (« WebSocket
   disconnected »), probablement un écart de version avec le Chromium distant. `chrome://inspect`
   (*Discover network targets* → *Configure…* → `localhost:9243`, et surtout pas *Port forwarding*) reste
   utile pour **voir** les onglets, pas pour les ouvrir.

   Éviter les créneaux où le pod utilise le navigateur : `linkedin-sync` (toutes les heures à :30,
   10 h-21 h en semaine) et le cron du briefing de 20:00. Mettre `linkedin-auto-responder` en pause ne
   se fait pas par `kubectl scale` : Argo CD (selfHeal) l'annulerait. Désactiver d'abord l'auto-sync de
   l'application si on veut vraiment le mettre en pause.
4. Avant de se connecter : si le profil a été amorcé depuis le Sealed Secret (ou contient une session
   morte), dans le DevTools de l'étape 3 : *Application* → *Storage* → **Clear site data**, puis recharger la
   page de connexion. Le profil porte sinon les cookies d'appareil du Mac (`bcookie`, `bscookie`, `JSESSIONID`) :
   le même jeton de session présenté avec deux empreintes d'appareil est révoqué par LinkedIn.
5. Se connecter (e-mail, mot de passe, 2FA) dans la vue, jusqu'à l'affichage du fil. Fermer ensuite
   l'onglet ouvert à l'étape 2 : `curl -s -X PUT http://localhost:9243/json/close/<ID>`.
6. Fermer DevTools et le tunnel. Rien à redéployer : le profil porte la session.
7. Contrôle : noter `linkedin_mcp_session_seeds_total` **avant** de commencer
   (`kubectl -n linkedin-mcp exec deploy/linkedin-mcp -- python -c "import urllib.request;print([l for l in urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode().splitlines() if l.startswith('linkedin_mcp_session_seeds_total')])"`),
   puis vérifier après coup qu'un `scrape_post` de test réussit et que le compteur n'a pas bougé. (Le
   log « AMORCÉE » n'apparaît qu'à l'initialisation du navigateur : son absence ne prouve rien.)

**Secours, profil vierge** (nouveau nœud, profil supprimé) : le pod amorce le profil depuis le Sealed
Secret. Sa session est souvent périmée et le profil porte les cookies d'appareil du Mac : refaire
aussitôt la procédure ci-dessus, **y compris le « Clear site data » de l'étape 4**.

Le fichier local du Mac (`just session`) ne sert plus qu'au développement local. Ne pas le sceller vers
k3s s'il a servi sur le Mac.

Avant de conclure à une session morte, écarter les autres causes classiques (voir le README) :
dépendance `linkedin-playwright-scraper` à mettre à jour, régression DOM LinkedIn
(cf. [post-mortems](post-mortem/)), Chromium absent (`uv run playwright install chromium`).

## Ne pas confondre avec l'OAuth

Deux authentifications **indépendantes**, aucune ne remplace l'autre :

| | Session Playwright | OAuth2 |
|---|---|---|
| Créée par | `create_scrape_session` / `create_session.py` | `authenticate` |
| Sert à | scraping feed/post, repost UI, invitations, messagerie | poster via l'API officielle, repost API |
| Stockage | `linkedin_session.json` | `linkedin_mcp/tokens/*.json` |
| Durée | cookies LinkedIn (à renouveler quand ça casse) | token ~2 mois |

Détail du flux OAuth : [docs/analysis/authentication-flow.md](analysis/authentication-flow.md).

## Sécurité

- Le fichier de session **équivaut à être connecté au compte**. Il n'est jamais committé :
  `linkedin_session.json` est dans `.gitignore`, et seuls les `*.sealed.yaml` (chiffrés pour
  le contrôleur Sealed Secrets du cluster) le sont.
- Permissions `600`, chemin par utilisateur hors du repo.
- Ne jamais le coller dans un chat, une issue, un ticket ou un log.
- En cas de fuite : LinkedIn → *Paramètres → Connexion et sécurité → Où vous êtes connecté* →
  déconnecter toutes les sessions, changer le mot de passe, puis recréer la session ici et
  re-sceller.
