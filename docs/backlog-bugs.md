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
