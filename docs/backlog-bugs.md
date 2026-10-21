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
via Hermes. La panne a été découverte à l'usage ; je n'ai pas vérifié si une alerte avait été émise.

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

## B-003 — Le scraper n'extrait pas les posts de l'utilisateur lui-même

- **Priorité** : moyenne (bloque le rejeu du critère 8 de la tranche 1 de Hermes)
- **Ouverte le** : 2026-10-21
- **Type** : bug

### Constat

`scrape_post` répond `No post extracted from detail URL` (journal de `linkedin-mcp`, `linkedin_scraper.scrapers.feed`)
pour deux posts de test écrits par l'utilisateur, publiés en « Connexions » (20 et 21/10/2026) : URL
`linkedin.com/posts/share-<id>-<suffixe>/` (20/10) puis `linkedin.com/feed/update/urn:li:activity:<id>/` (21/10, URN
tiré de la redirection du lien court). Même jour, témoin : `scrape_post` sur un post d'un tiers (`…ugcPost-…`) renvoie
4 311 caractères et l'URN d'activité. Le scraper est donc sain, et l'échec ne dépend pas de la forme de l'URL.

### Cause

Inconnue. HYPOTHÈSE: la page de détail d'un post de l'utilisateur lui-même, ou d'un post texte seul récent en
« Connexions », a une mise en page que l'extracteur ne reconnaît pas. À vérifier : lire l'extracteur de
`linkedin-playwright-scraper` (version déployée 4.7.7) et comparer avec le DOM d'un tel post ; essayer un post
« Public » ou avec une image.

## B-004 — Le worker rejette les URL de posts sans nom d'auteur (`/posts/share-<id>-xxxx`)

- **Priorité** : basse
- **Ouverte le** : 2026-10-21
- **Type** : bug (dépôt `linkedin-feed-scrapping`, `apps/worker/src/manual/normalizeUrl.ts`)

### Constat

Le lien court d'un post de l'utilisateur redirige vers
`linkedin.com/posts/share-<id>-<suffixe>/?highlightedUpdateUrn=urn:li:activity:<id d'activité>&…`. Le worker répond
`INVALID_URL` (« Ce lien n'est pas un post LinkedIn ») : `STRICT_POST_PATH` exige `<nom>-(share|ugcPost|activity)-<id>-…`.
L'identifiant du chemin n'est pas l'identifiant d'activité (le deviner donne « Post introuvable »), et
`cleanPermalink` retire la requête, donc le vrai URN d'activité (`highlightedUpdateUrn`) est perdu.

### Piste

Après résolution d'un lien court, lire `highlightedUpdateUrn` avant le nettoyage de la requête et construire
`feed/update/urn:li:activity:<id>`, qui passe déjà la regex. À éviter : relâcher la regex sans récupérer l'URN
d'activité, sous peine d'une clé de déduplication fausse (le cron et le partage manuel doivent produire la même
clé). Sans effet pour les posts de tiers, dont l'URL porte un nom d'auteur (cas courant).
