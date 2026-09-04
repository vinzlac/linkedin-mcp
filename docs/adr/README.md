# Architecture Decision Records

Ce répertoire contient les ADRs (Architecture Decision Records) du projet `linkedin-mcp`.

Un ADR documente une décision architecturale significative : le contexte qui l'a motivée, la décision prise, et ses conséquences.

## Format

Chaque ADR suit le format :
- **Status** : Proposed / Accepted / Deprecated / Superseded
- **Context** : Pourquoi cette décision était nécessaire
- **Decision** : Ce qui a été décidé
- **Consequences** : Trade-offs, avantages, inconvénients

## Index

| # | Titre | Status |
|---|-------|--------|
| [001](001-k3s-litellm-mcp-gateway.md) | Déploiement k3s exposé via le MCP Gateway LiteLLM (pas d'exposition directe) | Accepted |
| [002](002-claude-code-mcp-client-via-litellm-gateway.md) | Enregistrement de `linkedin-mcp` (prod) comme client MCP Claude Code via le Gateway LiteLLM | Accepted |
| [003](003-post-action-url-cascade.md) | Cascade d'URL et diagnostic de page pour les actions UI sur un post | Accepted |
