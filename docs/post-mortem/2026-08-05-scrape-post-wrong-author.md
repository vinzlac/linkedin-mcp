# Post-mortem : `scrape_post` MCP retournait le compte connecté comme auteur

**Date** : 2026-08-05
**Statut** : Résolu (correctif dans `linkedin_scraper`, pas dans ce repo)
**Symptôme utilisateur** : `scrape_post` retournait `author_name`/`author_url` du compte LinkedIn connecté ("Vincent Lacoste") au lieu de l'auteur réel du post, sur 3/3 posts testés (`/posts/...` et `/feed/update/...`) ; `scrape_feed` n'était pas affecté

---

## Ce qui s'est passé

Un correctif similaire avait déjà été fait le 23/06/2026 (auteur du post vs navbar du compte connecté). Le bug est réapparu début août : `scrape_post` renvoyait systématiquement le compte connecté comme auteur, sans erreur ni avertissement dans les logs du pod k3s.

## Cause

Régression **externe** (côté LinkedIn) sur la page de détail d'un post : les classes CSS attendues (`.feed-shared-actor`, `#global-nav`, etc.) ont été remplacées par des classes atomisées/hashées sur `/posts/...`, faisant échouer silencieusement tous les sélecteurs du correctif de juin — plus aucun élément ne matchait. Détail complet (root cause, investigation, correctifs, code) → post-mortem détaillé côté scraper :

→ [linkedin_scraper — scrape_post auteur incorrect (classes CSS hashées)](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/post-mortem/2026-08-05-scrape-post-wrong-author.md)

Ce repo dépend de `linkedin_scraper` publié sur PyPI sous `linkedin-playwright-scraper` (ADR-018). Le correctif est en version **4.0.1**.

## Correctif appliqué dans ce repo

| Fichier | Changement |
|---|---|
| `pyproject.toml` | Contrainte déjà `>=4.0.0`, aucun changement nécessaire |
| `uv.lock` | `linkedin-playwright-scraper` 4.0.0 → 4.0.1 |

## Test local (sans Claude)

```bash
cd ~/workspace/linkedin-mcp
uv sync
uv run python test_mcp_scrape_post.py   # ou test_linkedin_mcp.sh
```

## Prévention

- Après toute mise à jour de `linkedin-playwright-scraper`, revalider `scrape_post` sur au moins une URL `/posts/...` et une URL `/feed/update/...`.
- Un correctif d'extraction DOM basé sur des classes CSS LinkedIn peut cesser de fonctionner silencieusement (zéro élément matché, pas d'erreur) si LinkedIn change son système de classes — pas seulement en cas de renommage de libellé ou de bouton.

## Outils concernés

- `scrape_post` — impact direct
- `scrape_feed` — non affecté (page `/feed/` non concernée par la migration CSS observée)
