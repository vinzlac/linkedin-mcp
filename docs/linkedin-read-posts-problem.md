# Problème : Lecture des posts LinkedIn via API

## Objectif

Scraper les posts LinkedIn de son réseau (feed, posts de connexions).

## Pourquoi l'API officielle ne suffit pas

LinkedIn segmente les scopes OAuth2 en deux niveaux :

| Scope | Accès | Disponibilité |
|-------|-------|---------------|
| `w_member_social` | Créer des posts | Apps standard ✅ |
| `r_member_social` | Lire des posts | Marketing Developer Platform uniquement ❌ |

Le **Marketing Developer Platform (MDP)** est réservé aux entreprises partenaires LinkedIn. L'accès est manuel, long à obtenir, et rarement accordé aux développeurs individuels.

**Conséquence** : même avec un code correct et un token valide, l'API renvoie **403 Forbidden** sur tous les endpoints de lecture.

Les deux endpoints testés sont bloqués de la même façon :
- `GET /v2/ugcPosts` — scope `r_member_social` requis
- `GET /v2/shares` (legacy) — idem

## Solutions alternatives pour scraper les posts du réseau

### Option 1 — Scraping web avec Playwright (recommandé)
Automatiser un navigateur headless connecté à LinkedIn.

- Naviguer sur `https://www.linkedin.com/feed/` une fois authentifié
- Extraire le contenu du DOM (posts, auteurs, dates, likes)
- **Risques** : détection bot, changements de structure HTML, ToS LinkedIn
- **Contournements** : user-agent réaliste, délais aléatoires, session cookie réutilisé

### Option 2 — Proxycurl / RapidAPI
APIs tierces qui scrappent LinkedIn en proxy.

- [Proxycurl](https://nubela.co/proxycurl/) — payant, fiable, respecte les limites
- Donne accès aux posts publics d'un profil via `GET /v2/linkedin/person/posts`
- Ne donne pas accès au feed personnalisé (posts des connexions)

### Option 3 — Export de données LinkedIn (GDPR)
LinkedIn permet de télécharger ses propres données.

- `Paramètres → Confidentialité → Obtenir une copie de vos données`
- Inclut ses propres posts mais **pas** ceux du réseau

### Option 4 — Demander l'accès MDP
Formulaire officiel sur [LinkedIn Developer Portal](https://developer.linkedin.com/product-catalog).

- Délai : plusieurs semaines à mois
- Peu de chances d'aboutir pour un usage individuel

## Recommandation

Pour scraper les posts **du réseau** (feed + posts de connexions) :

**Playwright** est la seule approche réaliste à court terme.
Un projet comme [`linkedin-scraper`](https://github.com/joeyism/linkedin_scraper) ou une session Playwright avec les cookies de session LinkedIn est le point de départ le plus pragmatique.

## Solution en cours — Fork de linkedin-scraper

Le repo [joeyism/linkedin_scraper](https://github.com/joeyism/linkedin_scraper) a été forké vers [vinzlac/linkedin_scraper](https://github.com/vinzlac/linkedin_scraper) et cloné dans `/Users/vinz/workspace/linkedin_scraper`.

L'objectif est d'y ajouter un `FeedScraper` capable de scraper les N premiers posts du feed LinkedIn (`linkedin.com/feed/`), en s'appuyant sur le `CompanyPostsScraper` existant comme modèle.

Le détail de la démarche est documenté dans [`FORK_CONTEXT.md`](../../../linkedin_scraper/FORK_CONTEXT.md) du repo forké.

## Références

- [LinkedIn API Permissions](https://learn.microsoft.com/en-us/linkedin/shared/authentication/permissions)
- [Marketing Developer Platform](https://developer.linkedin.com/product-catalog)
- [ugcPosts endpoint](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/community-management/shares/ugc-posts-api)
