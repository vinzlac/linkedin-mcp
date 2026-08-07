# Post-mortem : `repost_post` (API + fallback UI) en échec systématique

**Date** : 2026-08-06 (correctifs), 2026-08-07 (déploiement + validation end-to-end)
**Statut** : Résolu et validé en production — repost réel publié avec succès via `repost_post` (API)
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

## Cause n°2 (UI fallback) — corrigée

`repost_ui.py` cherchait un bouton dont l'`aria-label` valait exactement `"Republier"` ou `"Repost"` (`CLICK_REPOST_ON_PAGE_JS`). Diagnostic confirmé en live via Claude for Chrome (inspection DOM sur `https://www.linkedin.com/posts/akshay-pachaar_.../`, session Vincent Lacoste authentifiée) :

```js
document.querySelectorAll('button')
// bouton Republier trouvé :
// aria-label: null   ← LinkedIn a retiré l'attribut
// innerText: "Republier"
// contient <svg id="repost-small">
```

LinkedIn a retiré `aria-label` du bouton Republier (devenu `null` sur tous les posts testés, y compris celui avec 13 républications visibles) — pattern déjà vu sur ce repo (cf. [post-mortem 2026-08-05](2026-08-05-scrape-post-wrong-author.md), classes CSS hashées) mais cette fois c'est l'attribut d'accessibilité lui-même qui a disparu, pas seulement les classes CSS. Le bouton J'aime (`like_ui.py`), lui, conserve un `aria-label` — ce qui explique pourquoi `like_post` continuait de fonctionner alors que le repost était cassé.

**Correctif** : `isPostRepostBtn()` dans `CLICK_REPOST_ON_PAGE_JS` et `CLICK_REPOST_IN_FEED_CARD_JS` matche désormais aussi sur le texte visible du bouton (`innerText === "Republier"/"Repost"`) et sur la présence de l'icône `svg#repost-small`, en plus de l'`aria-label` (gardé pour rétrocompatibilité si LinkedIn le rétablit).

**Validé en live** : après correctif, le clic sur le bouton ouvre bien le menu "Republier en donnant votre avis" / "Republier instantanément" sur le post testé. Le clic de confirmation (publication réelle) n'a pas été déclenché pendant le diagnostic — action publique laissée à une exécution explicitement demandée.

## Correctif appliqué dans ce repo

| Fichier | Changement | Commit |
|---|---|---|
| `linkedin_mcp/server.py` | `repost_post` : défaut `visibility=PostVisibility.PUBLIC` au lieu de la chaîne `"PUBLIC"` | `b7fe314` |
| `linkedin_mcp/linkedin/repost_ui.py` | `isPostRepostBtn()` (page post + carte feed) : matche aussi sur `innerText` et `svg#repost-small`, pas seulement `aria-label` | `7517b00` |

## Déploiement et validation end-to-end

Image `ghcr.io/vinzlac/linkedin-mcp:7517b00d33bad3b14bdd123acb80b31eb8b5d265` construite et déployée en prod (commit GitOps `1d85bea`, `kubectl get pods -n linkedin-mcp` confirme le pod sur cette image).

**Blocage de déploiement rencontré (sans rapport avec le code)** : le build CI est resté `queued` près de 45 minutes. Cause trouvée par inspection du cluster (`kubectl -n arc-runners get autoscalingrunnerset`) : le scale-set ARC `arc-runner-linkedin-mcp` avait une config incohérente **`MINIMUM RUNNERS=4` > `MAXIMUM RUNNERS=2`**. Un changement de `maxPods` sur le node k3s (110 → 200, pour lever un plafond de pods saturé à 110/110) a déclenché un redémarrage/recréation des runners ; les 4 runners recréés ont dépassé le plafond max=2, bloquant la création d'un runner dédié au job en attente. Suppression manuelle des 4 pods runners orphelins (`kubectl -n arc-runners delete pod ...`) pour forcer une reconciliation propre — le build est reparti et a réussi. La config `min > max` du scale-set reste à corriger côté GitOps pour éviter une récidive.

**Validation end-to-end réelle** : `repost_post` (API) appelé en direct depuis Claude Code sur le post Superlinked SIE (`urn:li:activity:7491046285483864065`) → succès immédiat, sans passage par le fallback UI :

```
Repost créé via API. ID : urn:li:share:7491451495649804288
```

Confirme que le bug n°1 (`visibility`) est bien résolu en prod. Le repost a aussi été vérifié absent avant publication via `GET /in/vincent-lacoste-590a145/recent-activity/all/` (dernière activité = repost d'un autre post du même auteur, pas celui-ci).

## Prévention

- Les tools qui transmettent un paramètre à une fonction non-pydantic (comme `RepostManager.repost`) doivent déclarer leurs défauts avec le vrai type (membre d'enum), pas une chaîne qui ressemble au type — FastMCP ne re-valide pas les défauts non fournis par l'appelant.
- Les sélecteurs basés sur un `aria-label`/libellé LinkedIn exact peuvent casser silencieusement sans erreur explicite côté scraper — seul le symptôme "bouton introuvable" remonte, sans indiquer si c'est un problème de session, de post, ou de dérive UI. LinkedIn peut retirer l'`aria-label` lui-même (pas seulement changer son libellé) — préférer des sélecteurs combinant plusieurs signaux (texte visible + icône SVG + aria-label) plutôt qu'un seul attribut.
- Diagnostiquer ce type de dérive UI nécessite une inspection DOM live (Claude for Chrome ou équivalent) — les tools MCP de scraping actuels ne renvoient pas de HTML brut, seulement des données extraites déjà interprétées.
- Un `AutoscalingRunnerSet` ARC avec `minimum > maximum` peut rester silencieusement fonctionnel tant que les runners existants ne sont pas recréés — le bug ne se déclenche qu'au prochain churn de pods (ex. changement de `maxPods`, redémarrage de node). À corriger côté GitOps dès qu'identifié, ne pas attendre le prochain incident.

## Outils concernés

- `repost_post` — cause n°1 corrigée, validée en prod avec un repost réel publié
- `repost_post_scrape` — cause n°2 corrigée (fallback non re-testé en prod, le fix API a suffi)
- `like_post` — non affecté, fonctionne (3/3 posts testés)
