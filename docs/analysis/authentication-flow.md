# Analyse : Mécanisme d'authentification LinkedIn MCP

## Type d'authentification

Le serveur MCP utilise un **OAuth2 Authorization Code Flow** standard.
Ce n'est pas une connexion via navigateur à linkedin.com — c'est une délégation d'accès via token.

## Flow détaillé

```
1. Appel de l'outil `authenticate` dans Claude Desktop
        ↓
2. Le serveur MCP génère une URL OAuth2 :
   https://www.linkedin.com/oauth/v2/authorization
     ?client_id=...
     &redirect_uri=...
     &scope=openid profile email w_member_social
     &state=<token_aléatoire>
        ↓
3. L'utilisateur ouvre cette URL dans un navigateur
   → LinkedIn demande connexion + autorisation de l'app
        ↓
4. LinkedIn redirige vers le redirect_uri avec un `code` temporaire
        ↓
5. Le serveur MCP échange ce code contre un access_token
   POST https://www.linkedin.com/oauth/v2/accessToken
        ↓
6. Token sauvegardé dans linkedin_mcp/tokens/{user_id}.json
        ↓
7. Toutes les requêtes API utilisent ce token :
   Authorization: Bearer {access_token}
```

## Ce que le MCP possède (et ne possède pas)

| Élément | MCP | Navigateur |
|---------|-----|------------|
| Access token OAuth2 | ✅ | ❌ |
| Identifiants LinkedIn | ❌ | ❌ (jamais transmis au MCP) |
| Session cookie linkedin.com | ❌ | ✅ (si connecté) |

Le MCP n'a jamais accès aux identifiants LinkedIn. Il reçoit uniquement un token d'accès limité aux scopes autorisés.

## Login/password : requis dans les deux cas

Les deux mécanismes nécessitent le login/password LinkedIn, mais de façon différente :

### MCP OAuth2 — interactif
- L'utilisateur saisit son login/password **lui-même** dans le formulaire LinkedIn qui s'ouvre dans le navigateur
- Le mot de passe n'est jamais stocké nulle part
- Le token résultant est sauvegardé dans `linkedin_mcp/tokens/{user_id}.json`
- À renouveler uniquement à expiration du token

### Playwright FeedScraper — session pré-existante
- Le mot de passe **ne doit pas** être stocké en dur dans un fichier de config (risque de sécurité)
- Approche retenue : connexion manuelle une fois dans un navigateur Playwright, puis sauvegarde des cookies dans `session.json`
- Les sessions suivantes réutilisent ces cookies via `browser.load_session("session.json")` → plus besoin du mot de passe
- Le repo `linkedin_scraper` implémente déjà ce pattern

## Deux sessions indépendantes

La connexion OAuth2 du MCP et une session Playwright sont **totalement séparées** :

- **MCP** → token Bearer sur `api.linkedin.com` (appels REST)
- **Playwright** → cookie de session sur `www.linkedin.com` (interface web)

## Implication pour le FeedScraper

C'est précisément pourquoi le fork [`vinzlac/linkedin_scraper`](https://github.com/vinzlac/linkedin_scraper) est nécessaire.

Le `FeedScraper` doit utiliser une **session navigateur** (cookies Playwright sur `linkedin.com/feed/`), car :
- L'API officielle ne permet pas de lire le feed (scope `r_member_social` bloqué)
- Le feed est une page web dynamique, pas un endpoint REST
- Playwright réutilise les cookies d'une session existante → mot de passe non stocké

Les deux mécanismes coexistent donc dans l'architecture globale :

```
Claude Desktop
    ├── linkedin-mcp  →  OAuth2 token  →  api.linkedin.com  (écriture)
    └── linkedin_scraper  →  session cookie  →  www.linkedin.com  (lecture feed)
```

## Références

- `linkedin_mcp/linkedin/auth.py` — implémentation du flow OAuth2
- `linkedin_mcp/config/settings.py` — scopes et endpoints configurés
- [docs/linkedin-read-posts-problem.md](../linkedin-read-posts-problem.md) — pourquoi l'API ne suffit pas
