# Post-mortem : `scrape_feed` MCP retournait une liste vide

**Date** : 2026-06-25  
**Statut** : Résolu (correctif dans `linkedin_scraper`, pas dans ce repo)  
**Symptôme utilisateur** : le MCP LinkedIn « ne fonctionne plus » dans Claude Desktop sur `scrape_feed`

---

## Ce qui s’est passé

Claude appelait `scrape_feed` ; le serveur MCP répondait sans erreur mais avec **aucun post** (ou message « Aucun post trouvé dans le feed »).

Le serveur MCP (`linkedin_mcp/server.py`) et la session Playwright étaient **OK** :

- Fichier session présent (`~/Library/Application Support/linkedin-mcp/linkedin_session.json`)
- `test_linkedin_mcp.sh` : initialize + tools/list OK
- Navigation feed : titre `Fil d'actualité | LinkedIn` (utilisateur connecté)

## Cause

Régression DOM côté **librairie** `linkedin_scraper` : les boutons « Republier » n’exposent plus le libellé en `innerText`, seulement en `aria-label`. Voir le post-mortem détaillé :

→ [linkedin_scraper — feed repost aria-label](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/post-mortem/2026-06-25-feed-repost-aria-label.md)

Ce repo dépend de `linkedin_scraper` en **editable** (`pyproject.toml`). Après `git pull` sur le scraper :

1. Redémarrer **Claude Desktop** (recharge le process MCP + le venv)
2. Retester : `scrape_feed(count=5)`

## Test local (sans Claude)

```bash
cd ~/workspace/linkedin-mcp
uv run python test_scrape_feeds.py 5 --dir output
bash test_linkedin_mcp.sh   # smoke + scrape_post si session OK
```

## Actions MCP si ça recasse

| Check | Commande / action |
|-------|-------------------|
| Session expirée | `create_scrape_session` ou `uv run python create_session.py` |
| Feed vide, session OK | Mettre à jour `linkedin_scraper`, voir post-mortem scraper |
| MCP non rechargé | Quitter complètement Claude Desktop et relancer |
| Playwright manquant | `uv run playwright install chromium` |

## Outils concernés

- `scrape_feed` — impact direct
- `scrape_post` — utilise la même extraction DOM (`_extract_posts_from_feed`) sur page détail ; bénéficie du même correctif d’ancrage boutons
