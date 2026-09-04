# ADR-003 : Cascade d'URL et diagnostic de page pour les actions UI sur un post

**Status** : Accepted

## Context

`like_post` et `repost_post` (chemin Playwright) naviguent vers la page d'un post puis cliquent le bouton correspondant. Ils acceptent trois formes d'entrée : une URL complète (`/posts/…` ou `/feed/update/…`), un `urn:li:activity:<id>`, ou un `urn:li:compkey:…` (carte de feed).

Le 2026-09-03, une tâche planifiée a signalé un comportement contradictoire sur **le même post** :

```
like_post("urn:li:activity:7500824506219909120")
→ Error: Bouton J'aime introuvable sur la page post (button_not_found).
  Le post n'existe peut-être plus ou la session a expiré.

like_post("https://www.linkedin.com/posts/…-share-7500824506219909120-mYgS/")
→ "Post liké via Playwright (page post)."   # 30 secondes plus tard
```

La cause n'est ni l'encodage de l'URN, ni un défaut d'attente d'hydratation — les deux hypothèses les plus naturelles. Elle était déjà écrite dans la docstring de `canonical_post_url()` :

> Le id numérique embarqué dans un permalien `/posts/{slug}-{id}-{suffix}/` est un **share ou ugcPost id**, qui n'est PAS toujours un `urn:li:activity:` valide — reconstruire `/feed/update/urn:li:activity:{that_id}/` peut atterrir sur une page « Post introuvable » alors que le permalien d'origine se charge très bien.

Autrement dit : la limite était connue et documentée, mais le code n'en tirait qu'une conséquence défensive (« ne pas synthétiser d'URL quand on a déjà une URL complète ») et aucune conséquence corrective. Dès que l'appelant ne disposait que d'un URN — exactement ce que produisait `scrape_feed` en reconstruisant un URN depuis un slug — une seule forme d'URL était tentée, et son échec était final.

Le message d'erreur aggravait le problème : `button_not_found` était rendu par « Le post n'existe peut-être plus ou la session a expiré », deux causes qui étaient toutes les deux fausses. Le diagnostic envoyait sur de fausses pistes au lieu de désigner la vraie.

Un second facteur, découvert en inspectant le feed live le même jour : le nouveau rendu LinkedIn ne scrolle plus la fenêtre mais un conteneur interne (`<main>`). Les `window.scrollBy(...)` du repli « carte feed » — celui qu'on emprunte justement pour un `urn:li:compkey:` — étaient devenus des **no-op silencieux** : la boucle rejouait cinq fois le même écran avant d'abandonner.

## Decision

### 1. Une cascade d'URL, pas une URL unique

`post_url_candidates(post_ref)` remplace `canonical_post_url()` comme source de vérité (cette dernière en devient le premier élément, pour les appelants qui n'ont besoin que d'une URL) :

1. l'URL fournie telle quelle, si c'en est déjà une — elle est faite pour fonctionner ;
2. les trois formes d'URN sur `/feed/update/` pour le même id : `activity`, `ugcPost`, `share`.

`like_ui` et `repost_ui` parcourent ces candidats et ne concluent à l'échec qu'après les avoir épuisés. Une session expirée coupe court immédiatement : aucune autre URL n'y changerait quoi que ce soit.

### 2. Un diagnostic d'état de page, pas un fourre-tout

Nouveau module `post_page.py`. Avant de conclure à l'absence d'un bouton, la page est sondée et l'état classé en un code parmi cinq :

| Code | Signification | Conduite à tenir |
|---|---|---|
| `session_expired` | redirection login / authwall | arrêt immédiat, relancer `create_scrape_session` |
| `post_not_found` | page de post indisponible | candidat suivant |
| `action_bar_absent` | page chargée sans barre d'action | candidat suivant |
| `action_bar_present` | barre présente mais bouton non trouvé | sélecteur probablement obsolète |
| `probe_failed` | sonde en échec | non bloquant |

Le message d'erreur final liste le diagnostic **par URL tentée**. `action_bar_present` est le code le plus utile : il distingue « LinkedIn a changé son DOM » de « on n'est pas sur la bonne page », deux causes qui appelaient auparavant la même phrase.

### 3. La cible du scroll est résolue à l'exécution

Nouveau module `feed_scroll.py`. `scroll_feed(page, dy)` essaie, dans l'ordre : le document, `<main>`, `[role=main]`, puis le plus grand conteneur défilable de la page ; il renvoie la stratégie retenue pour le diagnostic. Pas de `<main>` codé en dur : un prochain changement de rendu ne rendra pas le module muet, il changera la branche retenue.

### 4. Le permalien complet est l'entrée recommandée

Les docstrings des outils MCP recommandent explicitement de passer le `linkedin_url` renvoyé par `scrape_feed` plutôt qu'un URN reconstruit. C'est la seule forme d'entrée qui lève l'ambiguïté à la source.

## Consequences

**Positif**

- L'automatisation like/repost fonctionne quelle que soit la forme d'entrée, sans que l'appelant ait à connaître la distinction activity / share / ugcPost.
- Un échec produit désormais un diagnostic exploitable au lieu d'une phrase trompeuse ; la distinction `action_bar_present` / `action_bar_absent` oriente directement vers la bonne investigation.
- Le repli « carte feed » redevient fonctionnel.

**Coûts et limites**

- **Limite assumée, documentée dans le code** : un id numérique nu ne dit pas de quel type d'entité il relève, et les espaces d'ids `activity` / `share` / `ugcPost` sont distincts. Rien ne garantit formellement que les trois formes désignent le même post. Cette ambiguïté est inhérente à l'entrée, pas à la cascade — l'ancien code la portait déjà en ne tentant que la forme `activity`. La cascade ne l'aggrave pas ; passer le permalien complet la supprime.
- Un échec complet coûte désormais jusqu'à 4 navigations au lieu d'une (~30 s au lieu de ~8 s). C'est le prix à payer pour ne plus échouer sur un cas qui aurait réussi ; le chemin nominal (URL complète en premier candidat) reste à une seule navigation.
- La sonde d'état ajoute un `page.evaluate` par candidat en échec, et repose sur des libellés FR/EN (« page introuvable », « isn't available »…) : une session dans une troisième langue retomberait sur `action_bar_absent` plutôt que `post_not_found`. Dégradation acceptable — la conduite à tenir est la même dans les deux cas.

## Liens

- Post-mortem associé : [2026-09-03-feed-report-urn-and-ui-actions](../post-mortem/2026-09-03-feed-report-urn-and-ui-actions.md)
- Post-mortem antérieur sur ces mêmes chemins : [2026-08-06-repost-broken-api-and-ui](../post-mortem/2026-08-06-repost-broken-api-and-ui.md)
- ADR upstream sur la dérive du rendu : [linkedin_scraper ADR-020](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/adr/020-feed-dom-anchors-after-2026-09-rendering.md)
- Commits : `3757bda` (cascade + diagnostic), `34aa13b` (scroll), `e12777d` (bump scraper 4.4.0)
- Modules : `linkedin_mcp/linkedin/repost.py`, `post_page.py`, `feed_scroll.py`, `like_ui.py`, `repost_ui.py`
