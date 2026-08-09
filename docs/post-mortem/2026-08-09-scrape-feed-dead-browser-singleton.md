# Post-mortem : `scrape_feed` échoue en boucle après mort du navigateur Playwright en cache

**Date** : 2026-08-09
**Statut** : Résolu
**Symptôme utilisateur** : tâche planifiée non-interactive dans Claude.ai (pipeline Notion : mapping, upsert, repost reason). `scrape_feed` a échoué **6 fois de suite** avec `Page.goto: Target page, context or browser has been closed`. Résultat : 0 post inséré, 0 mis à jour, aucun like, aucun repost — le pipeline entier bloqué.

---

## Ce qui s'est passé

`_get_browser()` (`linkedin_mcp/server.py`) met en cache un `BrowserManager` Playwright dans un singleton module-level (`_browser_manager` / `_browser_initialized`) pour éviter de relancer un navigateur à chaque appel MCP. Le flag `_browser_initialized` n'était jamais revérifié une fois passé à `True` — si le process Playwright meurt côté serveur (crash, OOM, redémarrage du pod/CDP distant), le singleton reste marqué comme valide indéfiniment, et chaque appel réutilise la même page/navigateur mort.

Le message d'erreur (`Page.goto: ... has been closed`) laisse penser à une session LinkedIn expirée, mais **ce n'est pas une expiration de session** — le fichier de session sur disque (cookies/storage state) reste valide. C'est uniquement le *process navigateur* qui est mort. Le "fix normal" documenté (`create_scrape_session`, login manuel + 2FA) est donc inadapté ici : il n'a jamais été nécessaire de ré-authentifier, seulement de relancer Chromium et recharger la session existante — ce que `_get_browser()` sait déjà faire depuis zéro, mais ne le déclenchait jamais tant que `_browser_initialized` restait `True`.

Aggravant particulier aux tâches planifiées : aucune intervention humaine possible pour lancer `create_scrape_session` (navigateur visible, login manuel) dans un contexte non-interactif — le pipeline restait bloqué sans remède disponible.

## Cause

`_get_browser()` retournait le singleton en cache sur la seule base du flag booléen `_browser_initialized`, sans vérifier que le `Browser`/`Page` Playwright sous-jacent répond encore.

## Correctif appliqué dans ce repo

`linkedin_mcp/server.py` — nouvelle fonction `_browser_singleton_is_alive()` : vérifie `browser.is_connected()` et `not page.is_closed()` avant de réutiliser le singleton. Si mort, `_get_browser()` appelle `_close_browser_singleton()` puis relance un navigateur frais avec la session existante — automatiquement, sans authentification manuelle.

```python
def _browser_singleton_is_alive() -> bool:
    if _browser_manager is None:
        return False
    try:
        return (
            _browser_manager.browser.is_connected()
            and not _browser_manager.page.is_closed()
        )
    except RuntimeError:
        return False
```

`_get_browser()` utilise ce contrôle au lieu du seul flag `_browser_initialized`, et journalise un `warning` explicite ("navigateur mis en cache mort/déconnecté — relance automatique") pour distinguer ce cas d'une vraie session expirée dans les logs. Commit `7cfa719`.

## Blocage de déploiement rencontré (sans rapport avec ce bug)

Le déploiement du fix a révélé un second problème, préexistant : le build Docker échouait systématiquement (`uv sync --frozen --no-dev` → `Failed to determine installation plan`). Cause : un commit `91aaeee` (2 jours plus tôt, ajout des tools invitations/messaging) avait basculé `linkedin-playwright-scraper` d'une dépendance PyPI vers un chemin local éditable (`../linkedin_scraper`, `[tool.uv.sources]`) pour utiliser `InvitationScraper`/`MessagingScraper` avant leur publication — jamais annulé avant de merger sur `main`. Le dossier local n'existe pas dans le contexte de build Docker.

**Correctif** : `linkedin_scraper` republié sur PyPI en `4.1.0` (contient `InvitationScraper`, `MessagingScraper`, le fix du sélecteur repost). `linkedin-mcp/pyproject.toml` repointé sur `linkedin-playwright-scraper>=4.1.0` (registre), suppression du `[tool.uv.sources]`. Commit `f271d37`, déployé avec succès.

## Suivi : faux négatif "Not logged in" après relance automatique

Une fois le fix déployé et testé en live, `scrape_feed` a échoué une première fois avec `Not logged in. Please authenticate before scraping.` puis réussi au retry immédiat — un comportement différent du bug ci-dessus (message d'erreur distinct) mais **révélé plus souvent par ce même fix** : avant, le singleton ne redémarrait quasiment jamais (un seul cold start par vie du process serveur) ; après, chaque navigateur mort déclenche un nouveau cold start, donc bien plus fréquent.

**Cause** : `ensure_logged_in()` (`linkedin_scraper/scrapers/base.py`) appelle `is_logged_in()` juste après `navigate_and_wait(FEED_URL)`, qui n'attend que `wait_until="domcontentloaded"`. LinkedIn étant une SPA React, la barre de navigation (sélecteurs vérifiés par `is_logged_in()`) peut ne pas encore être hydratée à ce stade, surtout sur un navigateur qui vient de (re)démarrer sans bundles JS déjà en cache — d'où un faux négatif malgré une session parfaitement valide. Aucun retry n'existait avant ce check.

**Correctif** : `ensure_logged_in()` retente `is_logged_in()` jusqu'à 3 fois sur ~3s avant de lever `AuthenticationError`. Publié dans `linkedin_scraper` v4.1.1 (38 tests passés, aucune régression), `linkedin-mcp` bumpé sur `>=4.1.1`. Commit `885b117`, déployé et validé en live (`scrape_feed` a retourné 5 posts avec succès).

## Prévention

- Tout singleton de ressource externe (navigateur, connexion réseau, process enfant) mis en cache dans un serveur long-running doit être revérifié à chaque usage, pas seulement initialisé une fois — un flag booléen ne reflète que l'intention, pas l'état réel de la ressource.
- Distinguer explicitement (messages d'erreur, logs) "session expirée, ré-authentification nécessaire" de "process mort, relance automatique possible" — le premier bloque un pipeline non-interactif, le second ne devrait jamais le faire.
- Un check DOM juste après `wait_until="domcontentloaded"` sur une SPA doit prévoir un retry — le DOM initial ne garantit pas que le JS a fini de rendre l'UI, particulièrement sur un navigateur qui démarre à froid.
- Une dépendance locale éditable (`[tool.uv.sources]` avec `path=`) utilisée pour du dev doit être annulée avant de merger sur la branche qui alimente le build Docker/CI — sinon le build casse silencieusement au prochain push, sans lien évident avec le commit qui l'a introduite.
- Rendre un composant plus résilient (ici : auto-relance du navigateur) peut faire apparaître plus souvent un bug latent ailleurs (ici : la race `ensure_logged_in`) — à surveiller après tout fix de ce type.

## Outils concernés

- `scrape_feed` — impact direct, corrigé (singleton + race `ensure_logged_in`)
- Tous les autres tools passant par `_get_browser()` (`scrape_post`, `like_post`, `repost_post_scrape`, `list_pending_invitations`, etc.) — même bug latent, même correctif, bénéficient tous de la relance automatique et du retry de connexion

## Commits / versions

| Repo | Commit / tag | Contenu |
|---|---|---|
| `linkedin-mcp` | `7cfa719` | Fix singleton navigateur mort |
| `linkedin-mcp` | `f271d37` | Restaure dépendance PyPI (débloque le build) |
| `linkedin_scraper` | `v4.1.0` | Publie `InvitationScraper`/`MessagingScraper` (déjà sur master, jamais publiés) |
| `linkedin-mcp` | `885b117` | Bump vers `linkedin-playwright-scraper>=4.1.1` |
| `linkedin_scraper` | `v4.1.1` | Fix retry `ensure_logged_in` |
