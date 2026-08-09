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

`_get_browser()` utilise ce contrôle au lieu du seul flag `_browser_initialized`, et journalise un `warning` explicite ("navigateur mis en cache mort/déconnecté — relance automatique") pour distinguer ce cas d'une vraie session expirée dans les logs.

## Prévention

- Tout singleton de ressource externe (navigateur, connexion réseau, process enfant) mis en cache dans un serveur long-running doit être revérifié à chaque usage, pas seulement initialisé une fois — un flag booléen ne reflète que l'intention, pas l'état réel de la ressource.
- Distinguer explicitement (messages d'erreur, logs) "session expirée, ré-authentification nécessaire" de "process mort, relance automatique possible" — le premier bloque un pipeline non-interactif, le second ne devrait jamais le faire.

## Outils concernés

- `scrape_feed` — impact direct, corrigé
- Tous les autres tools passant par `_get_browser()` (`scrape_post`, `like_post`, `repost_post_scrape`, `list_pending_invitations`, etc.) — même bug latent, même correctif, bénéficient tous de la relance automatique
