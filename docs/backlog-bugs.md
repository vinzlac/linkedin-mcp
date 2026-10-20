# Backlog des bugs et besoins

Entrées à traiter, une section par entrée. Priorité : haute, moyenne, basse.

## B-001 — S'authentifier facilement pour pouvoir créer des posts

- **Priorité** : moyenne
- **Ouverte le** : 2026-10-20
- **Type** : besoin (la publication par l'outil `create_post` est inutilisable en pratique)

### Constat (2026-10-20)

Sur le serveur déployé dans k3s (namespace `linkedin-mcp`) :

- `create_post` répond `RuntimeError: Not authenticated. Please authenticate first`
  (`linkedin_mcp/server.py`, ligne 734). Le scraping (`scrape_post`, `scrape_feed`) fonctionne, parce
  qu'il passe par la session Chromium, pas par le jeton OAuth.
- `authenticate` échoue aussi : `Authentication failed: Failed to open browser. Please visit the URL
  manually: https://www.linkedin.com/oauth/v2/authorization?…&redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fcallback…&scope=openid+profile+email+w_member_social`.

### Causes lues dans le dépôt

- Le flux OAuth s'exécute **dans le pod** : il ne peut pas ouvrir de navigateur.
- Le `redirect_uri` est `http://localhost:3000/callback`, c'est-à-dire le `localhost` **du pod** : le
  navigateur de l'utilisateur, après consentement, tente de joindre le port 3000 de sa propre machine et
  échoue (sans `kubectl port-forward` du port 3000).
- `TOKEN_STORAGE_PATH=/secrets/oauth-tokens` est monté **en lecture seule** depuis le SealedSecret
  `linkedin-mcp-oauth-tokens` (`kubernetes/deployment.yaml`) : même un flux réussi ne pourrait pas écrire
  un nouveau jeton dans le pod. Un jeton ne peut entrer que par un SealedSecret régénéré.
- HYPOTHÈSE: le jeton du SealedSecret est absent, périmé ou lié à un autre `user_id`. Non vérifié
  (le contenu du Secret n'a pas été lu).

### Besoin

Pouvoir obtenir et renouveler un jeton OAuth valide (scope `w_member_social`) **sans manipulation
manuelle** : sans port-forward, sans régénérer un SealedSecret à la main, sans dépendre d'un navigateur
dans le pod.

### Pistes (non évaluées)

- Redirect public ou via le Mac de l'utilisateur (URI de redirection enregistrée dans l'application
  LinkedIn) et échange du code par le serveur.
- Flux `authenticate` qui imprime l'URL et accepte le `code` collé par l'utilisateur
  (`complete_authentication(code)`), sans callback local.
- Écrire le jeton dans un volume inscriptible (PVC ou Secret mis à jour par le serveur) au lieu d'un
  SealedSecret monté en lecture seule, avec rafraîchissement avant expiration.
- Publier par la session Chromium (comme `repost_post_scrape`) pour ne plus dépendre du jeton.

### Contexte d'usage

Rencontré en préparant l'essai d'injection (critère 8) de la tranche 1 de Hermes : un post de test devait
être publié par `create_post`. L'essai a été fait en publiant à la main.

## B-002 — Alerte Telegram quand la route IPv4 vers LinkedIn / Google est coupée ou rétablie

- **Priorité** : basse (à traiter plus tard)
- **Ouverte le** : 2026-10-20
- **Type** : besoin de supervision. Le travail se ferait dans `k3s-homelab` (`kubernetes/monitoring/`), cette
  entrée est ici faute d'un autre backlog.

### Constat (2026-10-20)

La route IPv4 du LAN vers `130.211.32.14` (load balancer de `lnkd.in` et `www.linkedin.com`, hébergé chez
Google) est coupée depuis chez l'utilisateur : `ETIMEDOUT` depuis les pods des deux nœuds et depuis le Mac
(`curl -4`), alors que l'IPv6 passe et que `curl -4 https://lnkd.in/` répond `301` depuis le partage 4G d'un
iPhone. Conséquence : le worker `linkedin-feed-scrapping` ne résout plus les liens courts `lnkd.in` (message
« Le lien court lnkd.in n'a pas pu être résolu (service injoignable) »), donc plus de partage par lien court
via Hermes. Aucune alerte n'a signalé la panne : elle a été découverte à l'usage.

### Besoin

Être prévenu sur Telegram quand cette route tombe, et surtout **quand elle revient**, sans la tester à la main.

### Pistes (non évaluées, rien n'a été lu ni testé côté monitoring)

- Réutiliser le `blackbox-exporter` déjà présent (`kubernetes/monitoring/templates/blackbox-exporter.yaml`, sondes
  TLS mail) avec un module HTTP forcé en IPv4 (`preferred_ip_protocol: ip4`, sans repli IPv6) sur
  `https://lnkd.in/`, réponse `301` attendue, redirection non suivie. La sonde doit partir **d'un pod** : c'est ce
  chemin qui compte.
- Règle Prometheus (`for` de quelques minutes) routée par l'adaptateur Alertmanager → Apprise → Telegram déjà en
  place (`kubernetes/monitoring/templates/alertmanager-apprise-adapter.yaml`). À vérifier : le message « résolu »
  est bien envoyé, c'est lui qui dit que c'est revenu.
- Variante légère : un moniteur TCP `130.211.32.14:443` dans Uptime Kuma (VM Debian). Moins représentatif, car il
  ne part pas d'un pod.
- Éviter une alerte bruyante pour une panne de l'opérateur : durée de `for` longue, une seule notification.
