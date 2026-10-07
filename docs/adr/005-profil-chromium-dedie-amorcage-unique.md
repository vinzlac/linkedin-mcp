# ADR-005 : profil Chromium dédié et amorçage unique de la session LinkedIn

- **Date** : 2026-10-07
- **Statut** : accepté
- **Remplace partiellement** : ADR-017 de k3s-homelab (Chromium partagé `chromium-cdp-host.openclaw`)

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
