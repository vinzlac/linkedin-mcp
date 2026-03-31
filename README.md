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

1. **Authenticate** — ask Claude: *"Authentifie-toi sur LinkedIn"*
   - A browser window opens → authorize → token saved in `linkedin_mcp/tokens/`
   - Token is valid for ~2 months

2. **Create a post** — ask Claude: *"Poste un message LinkedIn : [your text]"*

3. **Read posts (API)** — ask Claude: *"Récupère mes derniers posts LinkedIn"*
   - Only works if `r_member_social` scope is available (see [Limitations](#limitations))

4. **Feed (scraping)** — OAuth does **not** log you in on the website. For `scrape_feed`, first run **`create_scrape_session`** once (Chromium opens → you sign in → session file is written), then call **`scrape_feed`**. Alternative: `uv run python create_session.py`.

   **« Google Chrome for Testing » won’t go away?** The MCP server keeps that Playwright browser open between scrapes. Use **`close_scrape_browser`** when you’re done, or quit Claude Desktop to stop the MCP process entirely.

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

# Scrape feed → écrit par défaut dans output/feed.json (répertoire ignoré par git)
uv run python test_scrape_feed.py 5
```

Les exports locaux (ex. `output/feed.json`) vont dans le dossier `output/` : le contenu est ignoré par git, seul `output/.gitkeep` est versionné pour conserver le dossier dans le dépôt.

### Tool listing helper

`list_mcp_tools.py` queries `initialize` + `tools/list` against the local server and supports:

- default mode: formatted output with name, required/optional params, description
- `--short`: one-line summary per tool
- `--json`: machine-readable output (including schemas)

## Limitations

**Reading posts (`get_posts`, `get_posts_legacy`) is not available for standalone apps.**

Both `/v2/ugcPosts` (GET) and `/v2/shares` require the `r_member_social` scope, which is only granted through the **Marketing Developer Platform** — a restricted LinkedIn program not available to personal/standalone apps.

Workaround: export your LinkedIn data manually via **LinkedIn Settings → Data Privacy → Get a copy of your data**.

## Known issues fixed in this fork

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'linkedin_mcp'` | `pyproject.toml`: `packages = ["linkedin_mcp"]` |
| Server crash after `initialize` (protocol mismatch) | Updated `mcp >= 1.6.0` in `uv.lock` |
| `mcp.run()` without explicit transport | `server.py`: `mcp.run(transport="stdio")` |

## License

MIT License
