# ADR-001 : Déploiement k3s exposé via le MCP Gateway LiteLLM (pas d'exposition directe)

## Status

Accepted

## Context

`linkedin-mcp` tournait jusqu'ici uniquement en local (transport `stdio`, lancé par Claude Desktop). Le homelab k3s (`geekom-as6`) dispose déjà d'ArgoCD (GitOps), d'un registry privé, de BuildKit distant, et surtout d'un **LiteLLM déjà exposé publiquement** (`https://llm.code-advisors.site`) avec un **MCP Gateway** natif (virtual keys, permissions par serveur/tool).

Deux approches d'exposition étaient possibles pour rendre `linkedin-mcp` accessible depuis l'extérieur du homelab :

1. **Ingress direct** — exposer `linkedin-mcp` lui-même sur Internet (domaine dédié, TLS, auth à implémenter).
2. **Passer par le MCP Gateway de LiteLLM** — `linkedin-mcp` reste interne au cluster (`ClusterIP`), LiteLLM porte l'exposition TLS publique et l'authentification (virtual keys).

Voir le plan détaillé : [`docs/plan/deploy-k3s-argocd.md`](../plan/deploy-k3s-argocd.md).

## Decision

**Option 2** : `linkedin-mcp` reste strictement interne au cluster. Pas de `kubernetes/ingress.yaml`, pas de domaine dédié, pas d'auth applicative propre — le service `linkedin-mcp.linkedin-mcp.svc:80` n'est joignable que depuis l'intérieur du cluster. Le `Deployment` inclut :

```yaml
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 1000
```

avec `UV_CACHE_DIR=/tmp/uv-cache` (le seul chemin réellement inscriptible, `emptyDir`).

**Transport MCP** : passage de `stdio` (usage Claude Desktop local) à `streamable-http` en conteneur, contrôlé par une variable d'environnement `MCP_TRANSPORT` — `stdio` reste le défaut pour ne pas casser l'usage desktop existant.

**Exposition publique** : enregistrement dans `mcp_servers` de la config LiteLLM (`k3s-homelab/kubernetes/llm/litellm-configmap.yaml`), avec `available_on_public_internet: true` (voir note ci-dessous — ce flag ne concerne pas `linkedin-mcp` lui-même). Une **team + virtual key dédiées** portent le contrôle d'accès réel : `object_permission.mcp_servers: [linkedin_mcp]` + `mcp_tool_permissions` limité aux tools autorisés (lecture : `scrape_feed`/`scrape_post`/`get_posts` ; écriture : `like_post`/`repost_post`/`repost_post_scrape`/`create_post`). `authenticate`/`create_scrape_session` restent délibérément exclus — ces flux nécessitent un navigateur système, non fonctionnels dans un pod headless.

**Secrets** : trois `SealedSecret` (`linkedin-mcp-session`, `linkedin-mcp-oauth-tokens`, `linkedin-mcp-oauth-creds`) montés en lecture seule, scellés depuis les fichiers locaux existants via `scripts/seal-secrets.sh`.

## Consequences

**Avantages :**
- Aucune surface d'attaque directe sur `linkedin-mcp` — le seul point d'entrée public est LiteLLM, qui porte déjà TLS et l'authentification pour les autres modèles/services du homelab.
- Contrôle d'accès granulaire par tool (`mcp_tool_permissions`), pas juste par serveur — une clé compromise n'a accès qu'au sous-ensemble de tools qui lui a été explicitement accordé.
- Réutilise l'infra existante (ArgoCD, BuildKit, LiteLLM) plutôt que d'ajouter un nouveau point d'exposition à maintenir (DNS, cert-manager, etc.).

**Inconvénients / limites connues :**
- `available_on_public_internet: true` sur l'entrée `mcp_servers` de LiteLLM est un nom trompeur : il ne décrit pas l'exposition réseau de `linkedin-mcp` (qui reste `ClusterIP`), mais autorise les appelants dont l'IP est hors des plages internes (`mcp_internal_ip_ranges`) à voir/utiliser ce serveur MCP. Sans lui, même la master key LiteLLM se voit renvoyer une liste de tools vide, silencieusement (voir [post-mortem](../post-mortem/2026-07-26-k3s-litellm-mcp-gateway.md)).
- Refresh de tokens OAuth impossible en montage secret read-only (`save_tokens()` échouerait) — sans impact pratique puisque `authenticate()` a de toute façon besoin d'un navigateur système, jamais exécuté dans ce pod. Tokens scellés une fois depuis un run `authenticate()` local, à resceller à expiration (~2 mois, pas de refresh automatique dans le code).
- Cooldown et cache de permalien (`~/.cache/linkedin_scraper/`, ADR-016 côté `linkedin_scraper`) sont locaux au filesystem du pod — perdus à chaque redémarrage. Pas de PVC pour l'instant.
