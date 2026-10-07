# ADR-004 : Sessions de fil LinkedIn sur un onglet dédié

**Status** : Accepted

## Contexte

Le worker `linkedin-feed-scrapping` doit parcourir le fil LinkedIn par lots successifs (`begin_feed_session`, `next_feed_posts`, `end_feed_session`) en conservant la position de lecture d'un lot à l'autre. Spec côté worker : `docs/superpowers/specs/2026-10-04-lots-de-scraping-et-verdicts-design.md` (dépôt `linkedin-feed-scrapping`).

Or l'onglet partagé du navigateur singleton est **garé sur `about:blank` après chaque outil** (depuis le 2026-09-21) : toute position de scroll et tout contenu de fil y sont détruits entre deux appels. Il est aussi utilisé par tous les autres outils (likes, reposts, scrapes), sous un verrou qui sérialise le navigateur.

Deux contraintes supplémentaires :

- Chrome **gèle les onglets d'arrière-plan** : un onglet de fil laissé en second plan cesse de répondre, et la sonde de vivacité de l'onglet partagé échoue (cf. `linkedin_scraper` af51454).
- Le pod peut redémarrer en laissant des onglets LinkedIn orphelins dans un navigateur persistant.

## Décision

1. **Onglet dédié par session.** Une session de fil possède son propre onglet, distinct de l'onglet partagé : la position survit au garage de l'onglet partagé. Le registre (`feed_session_registry`) stocke la session, indexée par `session_id`.
2. **Verrou tenu par appel uniquement.** Le verrou du navigateur est pris le temps d'un appel d'outil, jamais entre deux lots : les autres outils restent utilisables pendant qu'une session est ouverte.
3. **Une seule session à la fois.** L'ancienne session est fermée **avant** que le nouveau fil ne se charge, pour ne jamais cumuler deux fils lourds en mémoire. Exception (2026-10-07) : si l'ancienne session a servi il y a moins de 2 minutes, elle appartient sans doute à un run en cours ; la nouvelle est alors refusée (« session de fil occupée ») et le client lit le fil en une fois (`scrape_feed`). Deux runs simultanés se volaient sinon leurs sessions (2026-10-06 : 4 rechargements du fil en 3 minutes).
4. **Expiration à 600 s d'inactivité.** Toute lecture repousse l'échéance ; une session abandonnée par un worker planté est récupérée et son onglet fermé.
5. **Purge à la fermeture du singleton.** Fermer le navigateur purge toutes les sessions et ferme leurs onglets.
6. **Premier plan rendu à l'onglet partagé** après chaque opération sur l'onglet dédié, afin qu'il ne soit jamais gelé en arrière-plan (af51454).
7. **Marqueur `window.name` et balayage des orphelins.** L'onglet dédié est marqué via `window.name`. Au prochain `begin_feed_session` (pas au redémarrage du pod lui-même), un balayage ferme les onglets marqués sans session connue. Il est **restreint aux onglets `linkedin.com`** : on ne touche jamais aux onglets étrangers.
8. **Aucune fuite d'onglet.** Annulation (`CancelledError`) et dépassements de délai ferment l'onglet dédié ; un onglet planté est fermé avant l'erreur `session de fil inconnue`. Un échec du marquage, lui, laisse l'onglet ouvert et enregistré mais non marqué : il reste fermé par l'expiration ou `end_feed_session` tant que le pod vit, mais le balayage ne le retrouve pas après un redémarrage du pod.
9. **Plafond de 300 posts par session.** Rien d'autre ne borne le DOM côté serveur (le MCP est aussi exposé à d'autres clients). Au plafond, la session est fermée et le lot est rendu avec `exhausted: true` ; un appel ultérieur sur cet identifiant reçoit `session de fil inconnue`.

## Conséquences

- La position de lecture survit entre les lots, sans re-scroller depuis le haut du fil.
- Coût mémoire : le DOM du fil grossit à chaque lot (virtualisation partielle seulement). Le nombre de cartes dans le DOM est **à surveiller** : `test_feed_session_live.py` l'imprime à chaque lot (`cartes dans le DOM=`) ; à relever lors d'essais réels pour calibrer la taille et le nombre de lots.
- Les autres outils ne sont pas bloqués entre deux lots, mais restent sérialisés pendant un lot.
- Une session ne survit ni à un redémarrage du pod ni à 600 s d'inactivité : le worker doit gérer l'identifiant inconnu en rouvrant une session.
- Dépend de `linkedin-playwright-scraper>=4.5.0` (`FeedScraper.open_feed()` et `scrape_next()`).
- Le balayage est une heuristique sur `window.name` : un navigateur partagé avec une autre instance du même marqueur verrait ses onglets fermés.
