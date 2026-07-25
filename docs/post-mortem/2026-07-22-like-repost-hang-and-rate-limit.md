# Post-mortem : `like_post`/`repost_post` bloqués, puis URL invalide, puis rate limit

**Date** : 2026-07-22
**Statut** : Résolu
**Symptôme utilisateur** : `like_post` semblait rester bloqué indéfiniment après l'ajout d'un `scrape_post` préalable ; une fois corrigé, `like_post`/`repost_post` échouaient avec « bouton introuvable » ; le debug a ensuite déclenché un rate limit LinkedIn

---

## Ce qui s'est passé

1. Un `scrape_post(post_url)` ajouté avant `like_post(post_url)` (pour forcer la navigation vers la page individuelle du post) a fait paraître `like_post` bloqué indéfiniment.
2. Une fois le hang corrigé, `like_post`/`repost_post` échouaient avec « bouton introuvable » — le post existait pourtant bien.
3. En creusant, un bug secondaire de libellé (« Republier instantanément » vs « Diffusez instantanément ») bloquait le repost même après le fix d'URL.
4. Le debug intensif (scrapes et actions répétés en boucle rapprochée) a fini par déclencher un rate limit LinkedIn.

## Cause

Détail complet (root cause, correctifs, code) → post-mortem détaillé côté scraper :

→ [linkedin_scraper — hang like/repost, mauvaise URL, rate limit](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/post-mortem/2026-07-22-rate-limit-hang.md)

En résumé :
- `LikeUI`/`RepostUI` retombaient sur un fallback « carte feed » (aller-retour `/feed/`, jusqu'à ~2 min) quand le bouton n'était pas trouvé sur la page post — perçu comme un hang côté client MCP.
- `_post_url()`/`normalize_post_url()` reconstruisaient une URL `feed/update/urn:li:activity:{id}/` à partir d'un ID de permalien `/posts/...` qui n'est pas garanti être un ID d'activity valide (share/ugcPost ≠ activity) → « Post introuvable ».
- `LINKEDIN_HEADLESS` valait `True` par défaut dans ce repo, contredisant l'ADR-002 du scraper (`headless=False` obligatoire contre la détection anti-bot).
- Pas de cache de permalien ni de plafond sur le fallback UI côté scraper → volume de clics automatisés élevé pendant les runs de test répétés.

## Correctifs appliqués

| Fichier | Changement |
|---|---|
| `linkedin_mcp/linkedin/like_ui.py` | Plus de fallback feed automatique ; retries bornés sur la page post ; skip navigation si déjà sur la bonne URL ; cooldown + pacing avant chaque like |
| `linkedin_mcp/linkedin/repost_ui.py` | Idem + fix libellé menu « instantané » ; cooldown + pacing avant chaque repost |
| `linkedin_mcp/linkedin/repost.py` | `canonical_post_url()` : navigue vers l'URL originale plutôt que de la reconstruire |
| `linkedin_mcp/server.py` | `asyncio.wait_for(timeout=100s)` autour de `like_post`/repost Playwright |
| `linkedin_mcp/config/settings.py` | `LINKEDIN_HEADLESS` par défaut remis à `False` (conforme ADR-002 scraper) |
| `linkedin_scraper/core/rate_limit_guard.py` (scraper) | Cooldown persistant inter-process + pacing des actions d'écriture |
| `linkedin_scraper/core/permalink_cache.py` (scraper) | Cache disque `urn -> permalink` |
| `linkedin_scraper/scrapers/feed.py` (scraper) | Retry sur la résolution du permalien ; cache + plafond sur le fallback UI |

## Test local

```bash
cd ~/workspace/linkedin-mcp
uv run python test_scrape_like_repost_feed.py            # dry-run (lecture seule)
uv run python test_scrape_like_repost_feed.py --execute   # like + repost réels sur le 1er post du feed
```

⚠️ `--execute` like/reposte réellement sur le compte configuré — espacer les runs manuels (le pacing automatique impose déjà un délai minimum entre deux actions d'écriture, mais n'élimine pas le risque en cas de tests répétés rapprochés).

## Prévention

- Vérifier que `LINKEDIN_HEADLESS` reste `False` après tout changement de config.
- Ne jamais reconstruire une URL de post à partir d'un ID extrait d'un permalien sans vérifier sa nature (share/ugcPost vs activity).
- En cas de rate limit : ne pas relancer immédiatement — le cooldown persistant (`~/.cache/linkedin_scraper/cooldown.json`) bloque désormais les tentatives prématurées automatiquement.

## ADR connexe

- [linkedin_scraper ADR-016 — Stratégie multi-couches contre la détection et le rate limit](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/adr/016-rate-limit-avoidance.md)
- [linkedin_scraper ADR-002 — headless=False obligatoire](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/adr/002-headless-false-required.md)

## Outils concernés

- `like_post`, `repost_post`, `repost_post_scrape` — impact direct (hang puis échec silencieux)
- `scrape_post`, `scrape_feed` — bénéficient du cache de permalien et du plafond de fallback UI côté scraper
