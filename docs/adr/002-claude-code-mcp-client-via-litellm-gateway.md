# ADR-002 : Enregistrement de `linkedin-mcp` (prod) comme client MCP Claude Code via le Gateway LiteLLM

## Status

Accepted

## Context

Après un correctif de bug (post-mortem [2026-08-05-scrape-post-wrong-author](../post-mortem/2026-08-05-scrape-post-wrong-author.md)) déployé en prod sur k3s, l'utilisateur a demandé un test live en conditions réelles depuis la session Claude Code de travail : scraper les 5 premiers posts du feed, liker et reposter le plus intéressant.

La session Claude Code utilisée pour développer/déployer le fix n'avait **aucun accès direct** aux tools `linkedin-mcp` (`scrape_feed`, `scrape_post`, `like_post`, `repost_post`) — ce serveur MCP est enregistré dans Claude Desktop (transport `stdio` local) et exposé en prod uniquement via le MCP Gateway LiteLLM (voir [ADR-001](001-k3s-litellm-mcp-gateway.md)), pas dans la configuration MCP de cette session Claude Code.

Deux approches ont été considérées pour donner à cette session un accès direct au serveur prod :

1. **`kubectl port-forward`** vers le service `linkedin-mcp.linkedin-mcp.svc:80` (ClusterIP), puis appels JSON-RPC MCP bruts en HTTP local.
2. **Enregistrer le serveur MCP prod comme client HTTP standard dans Claude Code** (`claude mcp add --transport http`), pointant sur l'URL publique du Gateway LiteLLM avec une virtual key scopée, exactement comme n'importe quel autre client MCP externe y accéderait.

L'option 1 a été **bloquée par le classifieur de sécurité auto-mode** de Claude Code : ouvrir un tunnel persistant vers un service prod portant une session LinkedIn authentifiée réelle, sans demande explicite de l'utilisateur pour ce tunnel précis, est jugé une action sensible nécessitant confirmation explicite. Plutôt que de contourner ce blocage, l'utilisateur a été interrogé, et a choisi de faire enregistrer le serveur via le canal prévu (option 2).

## Decision

Le serveur MCP prod est enregistré comme n'importe quel autre serveur MCP externe dans Claude Code, en scope `local` (projet `linkedin-mcp` uniquement, pas partagé globalement) :

```bash
claude mcp add --transport http linkedin-mcp-prod \
  "https://llm.code-advisors.site/linkedin_mcp/mcp" \
  --header "Authorization: Bearer <virtual-key>" \
  -s local
```

- **URL** : `https://llm.code-advisors.site/linkedin_mcp/mcp` — le nom de serveur `linkedin_mcp` (underscore) correspond à la clé `mcp_servers` réelle dans `litellm-configmap.yaml`, différente du nom de service Kubernetes `linkedin-mcp` (tiret) et du nom de repo/plan initial `linkedin-mcp/mcp` (voir la note à ce sujet dans ADR-001 et le [post-mortem 2026-07-26](../post-mortem/2026-07-26-k3s-litellm-mcp-gateway.md)).
- **Auth** : virtual key LiteLLM dédiée, stockée dans gopass (`vault/env/linkedin-mcp/litellm-virtual-key`), jamais en clair dans un fichier de config versionné. Claude Code stocke le header `Authorization` dans `~/.claude.json` (scope local, hors du repo).
- **Scope `local`** : ce serveur n'est enregistré que pour ce projet, pas globalement — évite qu'il apparaisse dans des sessions Claude Code sans rapport avec `linkedin-mcp`.
- **Nom distinct `linkedin-mcp-prod`** : évite toute confusion avec un éventuel futur serveur MCP `linkedin-mcp` en mode dev/local (`stdio`), et signale explicitement qu'il s'agit de la **prod** (session LinkedIn réelle, actions réellement visibles — like/repost).

Alternative écartée : `kubectl port-forward` reste possible en dernier recours (debug bas niveau, accès au service sans passer par LiteLLM), mais n'est plus la voie par défaut pour un usage applicatif normal des tools — le Gateway LiteLLM est le point d'entrée prévu pour ce type d'accès (ADR-001).

## Consequences

**Avantages :**
- Passe par le canal d'exposition officiel (ADR-001) plutôt que par un accès bas niveau au cluster — cohérent avec le modèle de sécurité déjà en place (auth par virtual key, scopes par tool via `mcp_tool_permissions`).
- Tools `linkedin-mcp` (prod) utilisables nativement dans cette session Claude Code comme n'importe quel autre outil, sans script intermédiaire ni appel JSON-RPC manuel.
- Réutilise la clé déjà provisionnée pour l'usage LiteLLM/Gateway — pas de nouveau secret à créer.

**Inconvénients / limites connues :**
- **Rechargement de session requis** : les tools d'un serveur MCP ajouté via `claude mcp add` en cours de session n'apparaissent pas dynamiquement dans l'index des tools déférés — un redémarrage de la session Claude Code (ou une nouvelle conversation) est nécessaire avant de pouvoir les appeler.
- **Actions réelles sur le compte LinkedIn réel** : `like_post`/`repost_post` via ce serveur exécutent des actions publiques, visibles, sur le compte LinkedIn réel de l'utilisateur (pas un environnement de test) — à traiter avec la même prudence que toute action de publication (confirmation explicite avant chaque like/repost, jamais automatique).
- La clé enregistrée (`-s local`) vit dans `~/.claude.json`, hors du repo — un `git clone` frais de `linkedin-mcp` ne recrée pas cet enregistrement ; il faut relancer la commande `claude mcp add` (avec la clé gopass) sur chaque nouvelle machine/session qui en a besoin.

## Liens

- [ADR-001 — Déploiement k3s exposé via le MCP Gateway LiteLLM](001-k3s-litellm-mcp-gateway.md)
- [Post-mortem 2026-08-05 — scrape_post auteur incorrect](../post-mortem/2026-08-05-scrape-post-wrong-author.md)
- [Post-mortem 2026-07-26 — déploiement k3s + enregistrement MCP Gateway](../post-mortem/2026-07-26-k3s-litellm-mcp-gateway.md)
