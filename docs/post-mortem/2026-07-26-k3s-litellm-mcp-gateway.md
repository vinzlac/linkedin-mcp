# Post-mortem : déploiement k3s + enregistrement MCP Gateway LiteLLM

**Date** : 2026-07-26
**Statut** : Résolu
**Symptôme** : plusieurs blocages successifs pendant le déploiement initial — pod en `CrashLoopBackOff`, puis `tools/list` renvoyant une liste vide (`{"tools":[]}`) sans aucune erreur dans les logs LiteLLM, y compris avec la master key.

---

## Ce qui s'est passé

Déploiement en 10 étapes ([plan complet](../plan/deploy-k3s-argocd.md)), la majorité s'est déroulée sans accroc (scaffolding, Dockerfile, secrets scellés, CI). Trois incidents ont nécessité un debug plus poussé.

### 1. Pod `CrashLoopBackOff` — cache `uv` en écriture sur filesystem read-only

Premier déploiement réel : le pod crashait en boucle avec `Read-only file system: /home/mcp/.cache/uv`. Le `Deployment` impose `readOnlyRootFilesystem: true` (seul `/tmp` est inscriptible, via `emptyDir`), mais `uv run` (utilisé au `CMD` du Dockerfile) tente d'initialiser son cache sous `$HOME/.cache/uv` à chaque invocation, indépendamment de `UV_NO_SYNC`.

**Fix** : `ENV UV_CACHE_DIR=/tmp/uv-cache` dans le Dockerfile.

**Vérification retenue pour la suite** : reproduire `readOnlyRootFilesystem` en local avant de pousser, plutôt que d'attendre un aller-retour CI + cluster complet :
```bash
docker run --read-only --tmpfs /tmp \
  -e TOKEN_STORAGE_PATH=/secrets/oauth-tokens -e LINKEDIN_SESSION_PATH=/secrets/session/linkedin_session.json \
  -v <tokens-dir>:/secrets/oauth-tokens:ro -v <session-dir>:/secrets/session:ro \
  linkedin-mcp:test
```

### 2. Double déclenchement du workflow CI — course d'acquisition de job ARC

Un second `gh workflow run` déclenché alors qu'un run précédent était encore en file d'attente a provoqué une annulation (`concurrency: cancel-in-progress: true`) au milieu de l'acquisition du job par le runner ARC. Le pod runner restait bloqué (`Skipping message Job... already acquired... Conflict`) sans jamais retenter.

**Fix** : `gh run cancel` sur le run bloqué, attendre la terminaison complète du pod runner (`kubectl get pods -n arc-runners`), puis ne déclencher qu'un seul run à la fois.

### 3. `tools/list` vide, silencieusement, même avec la master key LiteLLM

Le blocage le plus long. Après enregistrement de `linkedin_mcp` dans `mcp_servers` (config LiteLLM), `GET /v1/mcp/server` listait bien le serveur, mais tout appel `tools/list` (via `POST /mcp/`) renvoyait `{"tools":[]}` — **aucune erreur** dans les logs du pod LiteLLM, y compris avec la master key (accès admin complet).

**Étapes de diagnostic :**
1. Config initiale : format dict (`mcp_servers: linkedin_mcp: {...}`), `transport: streamable-http`, pas de `enable_mcp_registry`. → Silencieusement ignoré (`/v1/mcp/server` vide).
   → Doc LiteLLM (`docs.litellm.ai/docs/mcp_deployment`) : `general_settings.enable_mcp_registry: true` requis, `transport` doit être `"http"` et non `"streamable-http"`.
2. En corrigeant, découverte que `llm` est une Application **ArgoCD avec `selfHeal: true`** — mes `kubectl apply` directs sur la ConfigMap étaient silencieusement annulés (revert vers l'état committé dans `k3s-homelab`) en quelques secondes. Chaque test avant cette découverte partait donc d'une config non appliquée.
   → Toute modif sur une app ArgoCD-managée doit être **committée + poussée** dans `k3s-homelab` d'abord, puis `kubectl -n argocd annotate application <app> argocd.argoproj.io/refresh=hard --overwrite` pour forcer une sync immédiate (au lieu d'attendre le polling ~3 min).
3. Une fois la config réellement appliquée, essai du **format liste** (`mcp_servers: - server_name: ...`) montré par la doc upstream courante → `CrashLoopBackOff`, `AttributeError: 'list' object has no attribute 'items'`. L'image pinnée (`ghcr.io/berriai/litellm:v1.83.14-stable.patch.3`) est antérieure à cette doc et n'accepte que le format **dict**.
4. Retour au format dict, mais avec la clé `linkedin-mcp` (tiret) → nouveau crash : `Server name cannot contain '-'`. Renommé en `linkedin_mcp` (underscore) — sans impact sur le nom du Service k8s sous-jacent (`linkedin-mcp`, tiret, inchangé).
5. Pod stable, `/v1/mcp/server` liste bien `linkedin_mcp` — mais `tools/list` retournait toujours `[]`, **même avec la master key**. Comme les erreurs internes du gestionnaire MCP de LiteLLM ne sont loguées qu'en `verbose_logger.debug/warning` (désactivé, `set_verbose: false`), aucune trace exploitable dans les logs standard.
   - Test du header de debug `x-litellm-mcp-debug: true` → `x-mcp-debug-outbound-url: (unknown)`, peu concluant seul.
   - Test de connectivité directe pod-à-pod (`kubectl exec` dans le pod LiteLLM, requête HTTP brute vers `linkedin-mcp.linkedin-mcp.svc:80/mcp`) → **OK**, réponse MCP valide. Élimine tout problème réseau/DNS.
   - Lecture du code source directement dans le pod (`mcp_server_manager.py`, fonction `filter_server_ids_by_ip_with_info`) → révèle que `available_on_public_internet: false` ne décrit **pas** le serveur cible mais filtre les appelants dont l'IP n'est pas dans `mcp_internal_ip_ranges`. Ni `kubectl port-forward` (IP vue : `127.0.0.1`) ni un appel externe réel via `https://llm.code-advisors.site` ne tombent dans les plages internes configurées (`10.0.0.0/8` etc.) → serveur filtré pour tout appelant, y compris la master key.
   → Passage à `available_on_public_internet: true` (confirmé explicitement avec l'utilisateur avant application, changement de posture d'exposition sur un service partagé). Le contrôle d'accès réel reste porté par la team/clé (`object_permission` + `mcp_tool_permissions`), pas par l'IP de l'appelant.

**Résultat final** : `tools/list` retourne les 7 tools autorisés (préfixés `linkedin_mcp-`), et un appel `tools/call` sur un tool non autorisé (`authenticate`) est correctement rejeté (`"Tool 'authenticate' is not allowed for your key/team on server 'linkedin_mcp'"`) — confirmant que le scoping n'est pas juste cosmétique.

## Cause

Trois causes indépendantes, chacune silencieuse à sa façon :
1. Configuration Kubernetes (`readOnlyRootFilesystem`) plus stricte que ce que l'outillage Python (`uv run`) suppose par défaut.
2. Absence de garde-fou contre le double déclenchement concurrent d'un workflow CI avec `concurrency: cancel-in-progress`.
3. Documentation LiteLLM upstream en avance sur la version pinnée du service (format de config différent), combinée à une gestion d'erreur du gestionnaire MCP qui avale silencieusement les échecs (`verbose_logger.debug/warning`, désactivé par défaut) plutôt que de les remonter en erreur HTTP explicite.

## Prévention

- Toujours reproduire `readOnlyRootFilesystem` en local (`docker run --read-only --tmpfs /tmp`) avant de pousser une image destinée à un pod avec cette contrainte.
- Ne jamais déclencher un nouveau run CI tant qu'un précédent est encore `queued`/`in_progress` sur le même workflow.
- Pour toute app ArgoCD-managée (`selfHeal: true`) : commit + push avant tout test, jamais de `kubectl apply` direct en espérant qu'il persiste. `kubectl -n argocd annotate application <app> argocd.argoproj.io/refresh=hard --overwrite` pour forcer une sync immédiate.
- Vérifier le format de config attendu par la **version pinnée réelle** d'un service tiers (pas seulement la doc upstream la plus récente) avant un changement de schéma de config — en cas de doute, lire le code source directement dans le pod (`kubectl exec ... grep/sed` sur les fichiers du package installé) plutôt que de multiplier les essais-erreurs en production.
- Le champ `available_on_public_internet` de LiteLLM (et plus généralement, tout champ dont le nom suggère une propriété du *serveur cible*) mérite une lecture attentive de la doc/code avant de l'assumer — ici il gate en réalité les *appelants*, pas le serveur.

## ADR connexe

- [ADR-001 — Déploiement k3s exposé via le MCP Gateway LiteLLM](../adr/001-k3s-litellm-mcp-gateway.md)
- [linkedin_scraper ADR-018 — Publication PyPI](https://github.com/vinzlac/linkedin_scraper/blob/master/docs/adr/018-pypi-publication.md)
