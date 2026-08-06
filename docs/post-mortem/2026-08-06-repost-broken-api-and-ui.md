# Post-mortem : `repost_post` (API + fallback UI) en échec systématique

**Date** : 2026-08-06
**Statut** : Partiellement résolu — bug API corrigé, bug UI ouvert
**Symptôme utilisateur** : test live "scraper 5 posts, liker + reposter le plus intéressant" (voir [ADR-002](../adr/002-claude-code-mcp-client-via-litellm-gateway.md)). `like_post` a fonctionné sur 3 posts distincts. `repost_post` puis son fallback `repost_post_scrape` ont échoué sur **4 posts différents**, dans deux sessions séparées (Claude Code et Claude.ai, même serveur prod `linkedin-mcp-prod`).

---

## Ce qui s'est passé

Sur les 4 posts tentés (dont un avec 13 républications déjà visibles — le bouton existe donc bien sur la page), chaque appel a suivi le même chemin d'échec :

1. `repost_post` (API REST) → `Erreur inattendue repost : 'str' object has no attribute 'value'`
2. Fallback `repost_post_scrape` (UI Playwright) → `Bouton Republier introuvable sur la page post`

## Cause n°1 (API) — corrigée

`linkedin_mcp/server.py:363`, le tool `repost_post` déclarait :

```python
visibility: PostVisibility = "PUBLIC"
```

`PostVisibility` est un `str, Enum` — mais la valeur par défaut du paramètre était une chaîne brute `"PUBLIC"`, pas le membre d'enum. Quand l'appelant ne fournit pas explicitement `visibility` (cas de nos 4 tentatives), FastMCP transmet ce défaut tel quel à `RepostManager.repost()` (`linkedin_mcp/linkedin/repost.py`), qui appelle `visibility.value` dans `_build_payload()` — `str` n'a pas d'attribut `.value` → crash.

`create_post` a le même anti-pattern (`server.py:284`) mais ne plante pas : `visibility` y est passé dans `PostRequest`, un `pydantic.BaseModel`, qui coerce la chaîne en `PostVisibility` à la validation. `RepostManager.repost()` est une fonction Python nue sans validation pydantic — le défaut littéral n'est jamais coercé.

**Correctif** : `visibility: PostVisibility = PostVisibility.PUBLIC` dans `repost_post` (server.py).

## Cause n°2 (UI fallback) — non résolue

`repost_ui.py` cherche un bouton dont l'`aria-label` vaut exactement `"Republier"` ou `"Repost"` (`CLICK_REPOST_ON_PAGE_JS`). L'échec sur 4 posts différents, y compris un avec des républications visibles, indique que ce sélecteur ne matche plus rien sur la page post actuelle — pattern déjà vu sur ce repo (cf. [post-mortem 2026-08-05](2026-08-05-scrape-post-wrong-author.md), classes CSS hashées par LinkedIn) et anticipé dans le commentaire de `CLICK_INSTANT_REPOST_MENU_JS` sur le changement de libellés LinkedIn dans le temps.

Hypothèses non vérifiées (aucun accès à un screenshot/DOM live depuis cette session) :
- Nouveau libellé `aria-label` (ex. "Reposter" au lieu de "Republier", ou un texte incluant le compteur de republications).
- Bouton désormais imbriqué différemment (icône seule, `aria-label` porté par un enfant plutôt que le `<button>` lui-même).

**Pas d'investigation plus poussée dans cette session** : nécessite une inspection live de la page (capture d'écran + DOM), hors de portée des tools MCP actuellement exposés (`scrape_post`/`repost_post_scrape` ne renvoient pas de HTML brut). Le plan [`create-scrape-session-remote-mcp-tools.md`](../plan/create-scrape-session-remote-mcp-tools.md) (screenshot + action piloté à distance) donnerait la visibilité nécessaire pour diagnostiquer ce genre de dérive de sélecteur sans devoir relancer un `create_session.py` local.

## Correctif appliqué dans ce repo

| Fichier | Changement |
|---|---|
| `linkedin_mcp/server.py` | `repost_post` : défaut `visibility=PostVisibility.PUBLIC` au lieu de la chaîne `"PUBLIC"` |

## Prévention

- Les tools qui transmettent un paramètre à une fonction non-pydantic (comme `RepostManager.repost`) doivent déclarer leurs défauts avec le vrai type (membre d'enum), pas une chaîne qui ressemble au type — FastMCP ne re-valide pas les défauts non fournis par l'appelant.
- Les sélecteurs basés sur un `aria-label`/libellé LinkedIn exact peuvent casser silencieusement sans erreur explicite côté scraper — seul le symptôme "bouton introuvable" remonte, sans indiquer si c'est un problème de session, de post, ou de dérive UI. Documenté comme risque connu depuis le post-mortem 2026-08-05.

## Outils concernés

- `repost_post` — cause n°1 corrigée, fallback UI (cause n°2) toujours en échec
- `repost_post_scrape` — cause n°2, ouvert
- `like_post` — non affecté, fonctionne (3/3 posts testés)
