# Post-mortem : `scrape_feed` renvoyait un identifiant inutilisable, `like_post` / `repost_post` échouaient dessus

**Date** : 2026-09-03 (investigation et correctifs), 2026-09-04 (déploiement prod)
**Statut** : Résolu — image `ghcr.io/vinzlac/linkedin-mcp:42c385a…`, scraper `4.4.0`
**Impact réel observé** : tâche planifiée aboutie uniquement grâce à des contournements manuels ; un pipeline strict aurait échoué et pollué la base
**Repos concernés** : `linkedin-mcp` (ce repo) et [`linkedin_scraper`](https://github.com/vinzlac/linkedin_scraper) (upstream)

---

## Ce qui s'est passé

La tâche planifiée `linkedin-feed-11h` (scrape de 5 posts du feed → injection Notion → like + repost du meilleur post technique) a rendu un compte rendu détaillé de cinq anomalies. Elle a abouti, mais l'agent qui l'exécutait a dû contourner manuellement deux d'entre elles.

Deux relèvent de ce repo :

**`urn` inexploitable en aval.** Sur 5 posts, 4 avaient un `urn` valant `urn:li:compkey:…` — une clé de carte de feed, éphémère et dépendante de la session — au lieu d'un `urn:li:activity:…`. Utilisé comme clé d'unicité pour l'upsert Notion, ce compkey crée une ligne supplémentaire à **chaque** exécution au lieu de mettre à jour l'existante. Cause upstream, corrigée dans le scraper ([post-mortem dédié](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/post-mortem/2026-09-03-feed-rendering-drift.md)).

**`like_post` / `repost_post` en échec sur un URN.** Le contournement appliqué par l'agent — ré-extraire l'activity id du slug de `linkedin_url` et reconstruire `urn:li:activity:<id>` — produisait systématiquement :

```
like_post(post_url="urn:li:activity:7500824506219909120")
→ Error: Bouton J'aime introuvable sur la page post (button_not_found).
  Le post n'existe peut-être plus ou la session a expiré.
```

alors que le même post, passé sous forme d'URL `/posts/` complète, était liké et reposté sans problème trente secondes plus tard.

## Cause

Le message d'erreur était trompeur sur les deux causes qu'il avançait : le post existait, la session était valide.

L'id numérique porté par un slug `/posts/{slug}-share-{id}-{suffix}/` est un id de **share** (ou de ugcPost), pas nécessairement un id d'**activity**. En reconstruisant `/feed/update/urn:li:activity:<id>/` à partir de là, on atterrit sur une page sans barre d'action — d'où l'absence de bouton.

Le point notable : **cette limite était déjà écrite dans la docstring de `canonical_post_url()`**, mot pour mot. Le code en tirait une conséquence défensive (ne pas synthétiser d'URL quand on en a déjà une) mais aucune conséquence corrective : dès que l'appelant ne disposait que d'un URN, une seule forme d'URL était tentée et son échec était final. La connaissance était présente dans le repo ; elle n'était pas actionnée.

Le rapport initial proposait trois hypothèses par ordre de probabilité : construction d'URL différente selon le format d'entrée, encodage des `:` de l'URN, absence d'attente explicite. La première était la bonne, mais pour une raison différente de celle supposée : ce n'est pas la **vue** `/feed/update/` qui rend un DOM différent, c'est l'**id lui-même** qui ne désigne rien dans l'espace `activity`.

## Correctif appliqué dans ce repo

Détail des décisions et de leurs trade-offs dans [ADR-003](../adr/003-post-action-url-cascade.md).

- `post_url_candidates()` remplace l'URL unique : URL fournie telle quelle si c'en est une, puis les trois formes d'URN sur `/feed/update/` (`activity`, `ugcPost`, `share`). `like_ui` et `repost_ui` parcourent la cascade et ne concluent à l'échec qu'après l'avoir épuisée ; une session expirée coupe court.
- Nouveau module `post_page.py` : sonde l'état réel de la page et distingue `session_expired` / `post_not_found` / `action_bar_absent` / `action_bar_present` / `probe_failed`. Le message d'erreur liste le diagnostic **par URL tentée**.
- Docstrings des outils MCP : recommander le permalien complet (`linkedin_url` de `scrape_feed`) plutôt qu'un URN reconstruit.

Limite assumée et documentée dans le code : un id numérique nu ne dit pas de quel type d'entité il relève. La cascade ne lève pas cette ambiguïté — elle ne l'aggrave pas non plus, l'ancien code ne tentait que la forme `activity`.

## Dette trouvée en chemin : les `window.scrollBy` étaient des no-op

En inspectant le feed live pour le compte de l'investigation upstream, une régression silencieuse non signalée est apparue : **le feed ne scrolle plus la fenêtre mais `<main>`** (`document.scrollHeight == clientHeight`).

Les quatre `window.scrollBy(...)` de `like_ui` / `repost_ui` ne faisaient donc plus rien. Le repli « carte feed » — celui qu'on emprunte précisément quand l'entrée est un `urn:li:compkey:`, c'est-à-dire le cas le plus fréquent d'après le bug #1 — rejouait cinq fois le même écran avant d'abandonner.

Corrigé par un module `feed_scroll.py` dont la cible est résolue à l'exécution (document → `<main>` → `[role=main]` → plus grand conteneur défilable). Mesuré en live : `main.scrollTop` passe de 0 à 900, là où `window.scrollBy(0, 900)` laisse la page strictement inchangée.

## Prévention

- **Un message d'erreur qui énumère des causes doit pouvoir les distinguer.** « Le post n'existe peut-être plus ou la session a expiré » citait deux causes plausibles, toutes deux fausses, et a orienté le rapport de bug vers de mauvaises hypothèses (encodage, hydratation). Le coût n'est pas l'échec lui-même : c'est le temps d'investigation qu'il a fait perdre. D'où les cinq codes de diagnostic.
- **Une limite documentée dans une docstring n'est pas une limite traitée.** La cause exacte de ce bug était écrite dans le code depuis des semaines. Quand on documente un piège qu'on ne corrige pas, il faut soit le corriger, soit rendre l'échec explicite au moment où il se produit.
- **Un fallback qui ne peut pas échouer bruyamment finit par ne plus fonctionner du tout.** Le `window.scrollBy` no-op n'a jamais levé d'erreur : la boucle « scrollait » cinq fois et abandonnait proprement. Un fallback silencieux doit rapporter ce qu'il a réellement fait — `scroll_feed` renvoie désormais la stratégie retenue.
- **Vérifier en live avant de conclure.** Trois des cinq causes racines déduites par lecture de code étaient fausses (détail dans le post-mortem upstream).

## Outils concernés

`like_post`, `repost_post`, `repost_post_scrape`, `scrape_feed`, `scrape_post`

## Commits / versions

| Commit | Objet |
|---|---|
| `3757bda` | cascade d'URL + diagnostics de page |
| `34aa13b` | scroll du conteneur de feed |
| `e12777d` | bump `linkedin-playwright-scraper` 4.4.0 |
| `42c385a` | merge de la PR [#2](https://github.com/vinzlac/linkedin-mcp/pull/2) |
| `56b3b5a` | commit GitOps — image `ghcr.io/vinzlac/linkedin-mcp:42c385a…` |

Déploiement vérifié dans le pod : scraper `4.4.0`, champs `feed_compkey` / `top_comment` présents, cascade à 3 candidats, 5 codes de diagnostic, 19 outils exposés par `tools/list`.

> Détail opérationnel utile pour la prochaine vérification : dans le pod, le venv `uv` n'est pas sur le `PATH` (utiliser `/app/.venv/bin/python`), et le service écoute sur le port **80** (`kubectl -n linkedin-mcp port-forward svc/linkedin-mcp 18000:80`). ArgoCD poll environ toutes les 3 minutes ; pour forcer : `kubectl -n argocd annotate application linkedin-mcp argocd.argoproj.io/refresh=normal --overwrite`.
