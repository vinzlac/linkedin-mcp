# LinkedIn MCP Server

Post to LinkedIn and retrieve your posts directly from Claude Desktop.

## Features

- OAuth2 authentication flow (browser-based)
- Post text updates to LinkedIn
- Attach images and videos to posts
- Control post visibility (public/connections)
- Retrieve your recent posts (requires `r_member_social` — see [Limitations](#limitations))
- Secure token storage

## Tools

| Tool | Description |
|------|-------------|
| `authenticate` | Starts the OAuth2 flow — opens the browser, waits for callback |
| `create_post` | Creates a text post on LinkedIn with optional media and visibility |
| `get_posts` | Retrieves your recent posts via `/v2/ugcPosts` *(requires Marketing Developer Platform)* |
| `get_posts_legacy` | Retrieves your recent posts via legacy `/v2/shares` *(same restriction)* |
| `create_scrape_session` | Opens Playwright Chromium, manual web login, saves a per-user private session file for feed scraping |
| `scrape_feed` | Reads your LinkedIn home feed via the saved Playwright session *(not OAuth)* |
| `scrape_post` | Reads a single LinkedIn post by URL (`/posts/...` or `/feed/update/...`) |
| `repost_post` | Reposts a post (REST API, Playwright fallback if 403 on third-party posts) |
| `repost_post_scrape` | Reposts via Playwright UI only (requires scrape session) |
| `get_scrape_session_json` / `set_scrape_session_json` | Export/import Playwright session (cross-machine) |
| `close_scrape_browser` | Closes the Playwright browser kept open after feed scraping (see note below) |

## Prerequisites

### 1. LinkedIn Developer App

1. Go to https://www.linkedin.com/developers/apps → **Create app**
2. Associate a **LinkedIn Company Page** (required — personal `/in/` profiles are not accepted)
3. Under **Products**, add:
   - **Share on LinkedIn** — required for `create_post`
   - **Sign In with LinkedIn using OpenID Connect** — required for `authenticate`
4. Under **Auth**, add redirect URL: `http://localhost:3000/callback`

### 2. Local setup

```bash
git clone https://github.com/vinzlac/linkedin-mcp.git
cd linkedin-mcp
```

Create a `.env` file:

```env
LINKEDIN_CLIENT_ID=your_client_id
LINKEDIN_CLIENT_SECRET=your_client_secret
LINKEDIN_REDIRECT_URI=http://localhost:3000/callback
# Optional: override default per-user session path
# LINKEDIN_SESSION_PATH=/absolute/path/private/linkedin_session.json
# Scraping/repost headless (default true — no visible Chrome window)
# LINKEDIN_HEADLESS=true
```

By default, `LINKEDIN_SESSION_PATH` is per-user (outside the repo):
- macOS: `~/Library/Application Support/linkedin-mcp/linkedin_session.json`
- Linux: `~/.local/state/linkedin-mcp/linkedin_session.json`
- Windows: `%APPDATA%\\linkedin-mcp\\linkedin_session.json`

## Claude Desktop Configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "linkedin-mcp": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory",
        "/absolute/path/to/linkedin-mcp",
        "run",
        "python",
        "-c",
        "from linkedin_mcp.server import main; main()"
      ],
      "env": {
        "LINKEDIN_CLIENT_ID": "your_client_id",
        "LINKEDIN_CLIENT_SECRET": "your_client_secret",
        "LINKEDIN_REDIRECT_URI": "http://localhost:3000/callback"
      }
    }
  }
}
```

> **Notes:**
> - Use the **absolute path** to `uv` (`which uv` to find it — typically `/opt/homebrew/bin/uv` on macOS Apple Silicon)
> - Use the **absolute path** to the cloned repo in `--directory`
> - Credentials must be in `env` — Claude Desktop does not load `.env` files
> - Restart Claude Desktop after any config change
> - `MCP_TRANSPORT` defaults to `stdio` (used here). Set `MCP_TRANSPORT=streamable-http` only for the containerized/k3s deployment (see [Déploiement k3s](#déploiement-k3s) below).

### Verify before restarting

```bash
bash test_linkedin_mcp.sh
```

Expected output:
```
✅ .env trouvé
✅ Credentials OK
✅ Serveur répond correctement !
✅ Serveur MCP opérationnel
```

## Usage in Claude Desktop

### Deux authentifications distinctes

Ce serveur utilise **deux connexions LinkedIn indépendantes**. L'une ne remplace pas l'autre.

| Flux | Outil | Navigateur visible ? | Quand ? | Stockage |
|------|-------|----------------------|---------|----------|
| **OAuth API** | `authenticate` | Oui — **navigateur système** (Safari, Chrome…) | Poster via API, repost API (posts perso) | `linkedin_mcp/tokens/*.json` |
| **Session web** | `create_scrape_session` | Oui — **Google Chrome for Testing** (Playwright) | Première connexion web, ou session expirée | `~/Library/Application Support/linkedin-mcp/linkedin_session.json` |
| **Scraping / repost UI** | `scrape_feed`, `scrape_post`, `repost_post` | **Non** (headless par défaut) | Réutilise la session web déjà sauvegardée | — |

```
authenticate           → navigateur système (autorisation OAuth)
create_scrape_session  → Chrome for Testing visible (login manuel LinkedIn.com)
scrape / repost        → headless, sans fenêtre (cookies déjà en place)
```

> **Important :** `authenticate` (OAuth) **ne connecte pas** le site linkedin.com pour le scraping.  
> Inversement, `create_scrape_session` **ne sert pas** à poster via l'API officielle.

Pour forcer une fenêtre visible lors du scraping (si LinkedIn bloque le headless) : `LINKEDIN_HEADLESS=false` dans `.env` ou dans `env` de Claude Desktop.

1. **Authenticate (API)** — ask Claude: *"Authentifie-toi sur LinkedIn"*
   - Opens your **default browser** → authorize the app → token saved in `linkedin_mcp/tokens/`
   - Token valid ~2 months

2. **Create a post** — ask Claude: *"Poste un message LinkedIn : [your text]"*

3. **Read posts (API)** — ask Claude: *"Récupère mes derniers posts LinkedIn"*
   - Only works if `r_member_social` scope is available (see [Limitations](#limitations))

4. **Feed (scraping)** — run **`create_scrape_session`** once (visible Chromium → manual login → session file saved), then **`scrape_feed`** / **`scrape_post`** / **`repost_post`**. Alternative: `uv run python create_session.py`.

   **`create_scrape_session` is the only step that opens a visible Playwright window.** Day-to-day scraping runs headless (`LINKEDIN_HEADLESS=true` by default).

   **Browser still running?** The MCP server may keep a headless Playwright instance open between scrapes. Use **`close_scrape_browser`** when done, or quit Claude Desktop.

## Test scripts

```bash
# Test server startup + tools/list
bash test_linkedin_mcp.sh

# Pretty print MCP tools (detailed mode)
uv run python list_mcp_tools.py

# Pretty print MCP tools (compact mode)
uv run python list_mcp_tools.py --short

# Export MCP tools as JSON
uv run python list_mcp_tools.py --json

# Test authentication + create a post
uv run python test_create_post.py "My test post"

# Test authentication + read posts
uv run python test_get_posts.py

# Scrape plusieurs posts du feed → écrit par défaut dans output/feed.json
uv run python test_scrape_feeds.py 5

# Scrape un post unique via son URL
uv run python test_scrape_feed.py "https://www.linkedin.com/feed/update/urn:li:activity:123/"
```

Les exports locaux (ex. `output/feed.json`) vont dans le dossier `output/` : le contenu est ignoré par git, seul `output/.gitkeep` est versionné pour conserver le dossier dans le dépôt.

### Tool listing helper

`list_mcp_tools.py` queries `initialize` + `tools/list` against the local server and supports:

- default mode: formatted output with name, required/optional params, description
- `--short`: one-line summary per tool
- `--json`: machine-readable output (including schemas)

## Troubleshooting feed scraping

If `scrape_feed` / `repost_post` fails with **chrome-headless-shell missing** or **Executable doesn't exist**:

1. **Cause fréquente :** nettoyage du cache macOS (`~/Library/Caches/ms-playwright/`) ou outil type CleanMyMac.
2. **Réinstaller les navigateurs Playwright** (dans le clone `linkedin-mcp`) :
   ```bash
   cd /chemin/vers/linkedin-mcp
   uv run playwright install chromium
   ```
3. **Redémarrer Claude Desktop** complètement (le serveur MCP garde l'ancien état en mémoire).
4. **Vérifier en local** : `uv run python test_scrape_feeds.py 3 -`

If `scrape_feed` returns no posts in Claude Desktop but the server starts fine:

1. **Mettre à jour `linkedin-playwright-scraper`** (dépendance PyPI, voir `pyproject.toml`) et redémarrer Claude Desktop complètement.
2. **Regenerate session** if expired: `create_scrape_session` or `uv run python create_session.py`.
3. **Test locally** without Claude: `uv run python test_scrape_feeds.py 5 --dir output`
4. **Install Chromium** if Playwright complains: `uv run playwright install chromium`

Post-mortem (2026-06-25, empty feed / LinkedIn icon-only Repost buttons): [docs/post-mortem/2026-06-25-scrape-feed-empty.md](docs/post-mortem/2026-06-25-scrape-feed-empty.md)

## Limitations

**Reading posts (`get_posts`, `get_posts_legacy`) is not available for standalone apps.**

Both `/v2/ugcPosts` (GET) and `/v2/shares` require the `r_member_social` scope, which is only granted through the **Marketing Developer Platform** — a restricted LinkedIn program not available to personal/standalone apps.

Workaround: export your LinkedIn data manually via **LinkedIn Settings → Data Privacy → Get a copy of your data**.

## Known issues fixed in this fork

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'linkedin_mcp'` | `pyproject.toml`: `packages = ["linkedin_mcp"]` |
| Server crash after `initialize` (protocol mismatch) | Updated `mcp >= 1.6.0` in `uv.lock` |
| `mcp.run()` without explicit transport | `server.py`: defaults to `mcp.run(transport="stdio")`, switches to `streamable-http` when `MCP_TRANSPORT=streamable-http` |

## Déploiement k3s

Ce serveur peut aussi tourner comme pod interne au homelab k3s (`geekom-as6`), GitOps via **Argo CD** (dépôt `vinzlac/k3s-homelab`, ressource Application `linkedin-mcp`), exposé au monde extérieur uniquement via le **MCP Gateway de LiteLLM** (`https://llm.code-advisors.site`) — pas d'Ingress direct, service `ClusterIP` interne au cluster. Détails complets : [`docs/plan/deploy-k3s-argocd.md`](docs/plan/deploy-k3s-argocd.md).

- **Image** : `ghcr.io/vinzlac/linkedin-mcp` (tags `:<sha>` et `:main`). Après chaque build, le workflow commit `kubernetes/deployment.yaml` avec le tag SHA (`paths-ignore: kubernetes/**` évite une boucle).
- **Namespace** : `linkedin-mcp`, service `ClusterIP` sur le port `8000`.
- **CI** : build sur runner in-cluster (**Actions Runner Controller**, scale set `arc-runner-linkedin-mcp`) via **BuildKit** (`ClusterIP`). Secret requis : `BUILDKIT_HOST=tcp://buildkitd.cicd.svc.cluster.local:1234`, configuré via `./scripts/setup-github-actions.sh`.
- **Secret pull GHCR** (si package privé) :
  ```bash
  kubectl create secret docker-registry ghcr-pull \
    --namespace=linkedin-mcp \
    --docker-server=ghcr.io \
    --docker-username=TON_LOGIN_GITHUB \
    --docker-password=TON_PAT_READ_PACKAGES \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
  Ou depuis `k3s-homelab` : `GHCR_PULL_NAMESPACE=linkedin-mcp ./scripts/create-ghcr-pull-secret.sh`.
- **Doc contexte homelab / CI** (pour un assistant IA ou un contributeur) : [`install-k3s.md`](install-k3s.md), régénérable depuis `k3s-homelab` via `./scripts/update-app.sh linkedin-mcp`.
- **Rafraîchir le squelette** (fichiers `.github/`, `kubernetes/`, `scripts/` ajoutés depuis le template) depuis `k3s-homelab` : `./scripts/sync-app.sh linkedin-mcp`.

## License

MIT License
