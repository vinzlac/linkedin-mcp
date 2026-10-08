# ADR-005 : profil Chromium dédié et amorçage unique de la session LinkedIn

- **Date** : 2026-10-07
- **Statut** : accepté
- **Remplace partiellement** : ADR-017 de linkedin_scraper (`docs/adr/017-remote-cdp-homelab-chromium.md`, CDP distant vers le Chromium partagé `chromium-cdp-host.openclaw`)

## Contexte

Le 2026-10-07, après un redémarrage du parc, la session LinkedIn est morte (premier échec à 12:30),
puis une session neuve a été révoquée en moins de 6 minutes. Trois causes se combinaient :

- le profil du Chromium était partagé avec OpenClaw ;
- à chaque relance du navigateur, `load_session()` réinjectait **sans effacer** les cookies figés du
  Sealed Secret par-dessus ceux que LinkedIn avait rafraîchis ;
- la session avait été créée et utilisée sur le Mac avant d'être injectée.

Le contexte isolé (`new_context`) reste exclu : chaque contexte neuf déclenche une vérification de
sécurité, comme lors de l'incident du 2026-09-04.

## Décision

1. Instance Chromium **dédiée** `chromium-cdp-linkedin` sur gpu-node (9242/9243), profil non partagé,
   sans redémarrage préventif.
2. Le **profil fait foi**. `linkedin-playwright-scraper` ≥ 4.6.0 n'injecte le fichier de session que si
   le profil n'a pas de `li_at` valide, après effacement des cookies LinkedIn.
3. La session se crée et se renouvelle **dans ce Chromium**, via DevTools par tunnel SSH. Le Sealed
   Secret n'est plus qu'un amorçage de secours.
4. Chaque amorçage est compté (`linkedin_mcp_session_seeds_total`) et alerte (`LinkedinSessionSeeded`).

## Conséquences

- Les redémarrages du pod, du navigateur ou du nœud ne touchent plus la session.
- Le renouvellement demande un tunnel SSH et Chrome sur le Mac, et non plus `just session` puis un
  rescellement.
- Un Chromium de plus sur gpu-node (quelques centaines de Mo de RAM).
- Le profil partagé d'OpenClaw garde d'anciens cookies LinkedIn inutiles. Nettoyage facultatif.
- Limites de l'alerte `LinkedinSessionSeeded` : un amorçage n'a lieu qu'à la recréation du navigateur
  (pas de redémarrage préventif), donc une révocation peut rester invisible plusieurs jours. L'alerte se
  résout seule après ~15 min. La mort réelle de la session est couverte par `LinkedinSyncErrorSpike`
  (linkedin-auto-responder).
- Exposition résiduelle : `range=192.168.1.153/32` admet tous les pods du Geekom (SNAT), et tout
  processus local de gpu-node (DaemonSets `hostNetwork` compris) joint `127.0.0.1:9243`. Acceptable pour
  le homelab.

## Validation

Relevé du 2026-10-08 vers 11:20 UTC, soit ~18 h après la mise en service (2026-10-07 17:30 UTC) :

- `linkedin_mcp_session_seeds_total` = 0 : le profil a gardé sa session, aucun réamorçage.
- Plus aucun « Not logged in » dans les logs de `linkedin-mcp` ni de `linkedin-sync`. La seule
  erreur de `linkedin-sync` depuis est un « navigateur occupé » (contention du verrou, pas
  d'authentification) ; les synchronisations suivantes sont complètes.
- Cron `linkedin-feed-daily` du 2026-10-07 20:00 (run `9175953d`) : terminé, 50 posts lus, 3 nouveaux.
- `scrape_post` sur un permalien `/posts/…-ugcPost-…` (avec et sans `utm_*`/`rcm`) : réussi en ~4 s.

À compléter après le cron du 2026-10-08 20:00 pour couvrir 24 h.

## Retour arrière

Revert du merge sur `main` : l'URL CDP et l'image reviennent ensemble. Ne jamais faire tourner une image
≥ 4.6.0 contre le profil partagé 9222 (OpenClaw) : le `clear_cookies` filtré efface puis réajoute **tous**
les cookies du profil.
