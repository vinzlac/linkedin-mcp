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
| Sur le cluster | — (n8n porte la credential) | Sealed Secret `linkedin-mcp-session` monté dans le pod |

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

## Propager la session vers l'instance k3s

Le pod ne peut pas ouvrir de fenêtre de login : il consomme la session créée en local.

### Voie normale — Sealed Secret

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

### Voie rapide — outils MCP

Sans kubeseal, la session peut être transférée de machine à machine :

- **`get_scrape_session_json`** — renvoie le JSON brut de la session locale (valide le JSON
  avant de le rendre) ;
- **`set_scrape_session_json`** — écrit ce JSON dans la session de l'instance visée.

Pratique pour dépanner à chaud, mais **non persistant** : au prochain redémarrage du pod, le
fichier monté depuis le Sealed Secret reprend la main. Repasser par `seal-secrets.sh` pour
que ça tienne.

## Renouveler la session

Les cookies LinkedIn expirent, et LinkedIn peut invalider une session (changement de mot de
passe, déconnexion globale, détection d'anomalie).

**Symptômes** : `scrape_feed` renvoie 0 post alors que le serveur démarre bien ; redirection
vers la page de login dans les traces ; outils invitations/messagerie vides.

**Procédure** : identique à la création — `just session`, vérifier avec `just test-feed 3`,
puis re-sceller et redéployer si l'instance k3s est concernée.

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
