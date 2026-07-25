# Déploiement de `linkedin-mcp` sur k3s via ArgoCD (derrière LiteLLM)

Ce document est un guide d'implémentation destiné à Claude Code pour déployer le serveur MCP `linkedin-mcp` sur le homelab k3s (`geekom-as6`), avec CI/CD GitOps via ArgoCD, exposé de façon sécurisée derrière le MCP Gateway de LiteLLM (déjà en place).

## Contexte

- `linkedin-mcp` tourne aujourd'hui en local (transport `stdio`, lancé par Claude Desktop), navigateur Playwright soit local (`headless=False`, ADR-002 côté `linkedin_scraper`) soit connecté en CDP au Chromium partagé du homelab (`LINKEDIN_CDP_URL`, ADR-017).
- Le homelab k3s a déjà : ArgoCD (GitOps, sync polling sans webhook — ADR-0021), un registry privé (`geekom-as6:30500` / `registry.registry.svc.cluster.local:5000`), un BuildKit distant in-cluster, un pattern de scaffolding pour repos externes (`scripts/create-app.sh --existing-repo`), et **LiteLLM déjà exposé publiquement** (`https://llm.code-advisors.site`) avec un **MCP Gateway** natif (virtual keys, permissions par serveur/team/org, et **scopes par tool** — `allowed_tools`, `mcp_tool_permissions`).
- Décision actée : ne pas exposer `linkedin-mcp` directement sur Internet ; il reste interne au cluster (`ClusterIP`), c'est LiteLLM qui porte l'auth (virtual key) et l'exposition TLS.
- Décision actée : ne pas utiliser `mcp-gateway-registry` (AWS/agentic-community) — redondant avec LiteLLM pour cette échelle (voir échanges précédents).

**Prérequis déjà en place, ne pas refaire** :
- `LINKEDIN_HEADLESS=False` par défaut (ADR-002 respecté)
- Cooldown / pacing anti-rate-limit (`linkedin_scraper/core/rate_limit_guard.py`)
- Cache de permalien (`linkedin_scraper/core/permalink_cache.py`)
- Connexion CDP au Chromium homelab (`BrowserManager(cdp_url=...)`, ADR-017) — testée et fonctionnelle sur `http://192.168.1.153:9222`
- User-agent du Chromium distant corrigé (retrait de `HeadlessChrome`) côté `k3s-homelab`

---

## Étape 1 — Scaffolding côté `k3s-homelab`

Depuis `~/workspace/k3s-homelab` :

```bash
./scripts/create-app.sh --existing-repo ~/workspace/linkedin-mcp
```

Répond aux prompts : nom d'app `linkedin-mcp`, stack `custom` (pas de stack pré-faite adaptée à FastMCP), namespace `linkedin-mcp` (dédié, pas dans `openclaw` — isolation des secrets LinkedIn).

Cela crée (dans `k3s-homelab`) :
- `kubernetes/argocd/applications/linkedin-mcp.yaml` (Application ArgoCD, `automated: {prune: false, selfHeal: true}`, `CreateNamespace=true`)
- `applications/linkedin-mcp-meta.yaml` (entrée registry)

Puis, toujours depuis `k3s-homelab` :

```bash
./scripts/sync-app.sh linkedin-mcp            # copie kubernetes/, .github/workflows/, scripts/ dans le clone linkedin-mcp
./scripts/add-deployment-standard.sh linkedin-mcp
./scripts/update-github-workflow.sh linkedin-mcp
```

Ne pas lancer `preflight-app.sh` avant d'avoir terminé les étapes 2-5 (le premier `preflight` échouera tant que Dockerfile/deployment ne sont pas adaptés).

## Étape 2 — Dockerfile (`linkedin-mcp/Dockerfile`)

Partir du canevas `custom` généré, adapter :

```dockerfile
FROM python:3.12-slim

# uv gère les deps (voir Justfile/pyproject.toml existants)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY linkedin_mcp ./linkedin_mcp
# linkedin_scraper est une dépendance editable locale (ADR-015 côté linkedin_scraper) —
# vendorer le package dans l'image plutôt que de dépendre d'un chemin relatif ../linkedin_scraper.
COPY vendor/linkedin_scraper ./vendor/linkedin_scraper

RUN uv sync --frozen --no-dev
# PAS de `playwright install chromium` — connexion CDP au Chromium partagé (LINKEDIN_CDP_URL),
# aucun navigateur local dans ce pod (voir ADR-017 linkedin_scraper).

RUN useradd -u 1000 -m mcp
USER mcp

EXPOSE 8000
CMD ["uv", "run", "python", "-m", "linkedin_mcp.server"]
```

**Point d'attention à résoudre au moment de l'implémentation** : `linkedin_scraper` est actuellement référencé en `path = "../linkedin_scraper", editable = true` (`pyproject.toml`). Un build Docker n'a pas accès à `../linkedin_scraper` (hors contexte de build). Deux options :
1. Vendorer une copie de `linkedin_scraper` dans `linkedin-mcp/vendor/` au moment du build (script de sync manuel ou submodule git), et changer le `path` en conséquence pour le contexte Docker uniquement.
2. Publier `linkedin_scraper` sur un registry de paquets privé (PyPI interne) — mais ADR-015 de `linkedin_scraper` a justement écarté cette option pour l'usage desktop. À réévaluer si le vendoring s'avère pénible.

Trancher cette option en premier avant d'écrire le Dockerfile définitif.

## Étape 3 — Transport MCP : `stdio` → `streamable-http`

Dans `linkedin_mcp/server.py`, fonction `main()` :

```python
def main():
    ...
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
```

(Actuellement `mcp.run(transport="stdio")` — garder `stdio` disponible en local via une variable d'env ou un flag CLI, pour ne pas casser l'usage desktop existant avec Claude Desktop.)

## Étape 4 — `kubernetes/deployment.yaml` et `service.yaml`

- **Pas d'Ingress** (supprimer `kubernetes/ingress.yaml` généré par le template — le service reste interne).
- `Service` : `ClusterIP`, port `8000`.
- `Deployment` :
  - `env` : `LINKEDIN_CDP_URL=http://chromium-cdp-host.openclaw.svc:9222` (ou variante pod selon dispo du moment)
  - Montage des secrets (étape 5) en volume : session Playwright + tokens OAuth
  - `runAsUser: 1000`, `readOnlyRootFilesystem` à évaluer (Playwright/uv peuvent nécessiter `/tmp` inscriptible — prévoir un volume `emptyDir` sur `/tmp` si besoin)
  - Resources : léger côté ce pod (pas de Chromium local) — `requests: {cpu: 100m, memory: 256Mi}`, `limits: {cpu: 500m, memory: 512Mi}` à ajuster après un premier run

## Étape 5 — Secrets (SealedSecret)

Secrets nécessaires dans le pod, tous scellés (même pattern que `seal-openclaw-secret.sh` / le sealed secret ArgoCD déjà fait) :

| Secret | Contenu | Usage |
|---|---|---|
| `linkedin-mcp-session` | `linkedin_session.json` (storage state Playwright) | Session LinkedIn scraping |
| `linkedin-mcp-oauth-tokens` | tokens OAuth LinkedIn (`linkedin_mcp/tokens/*.json`) | API officielle (create_post, repost API) |
| `linkedin-mcp-oauth-creds` | `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_REDIRECT_URI` | Auth OAuth |

Script à créer dans `linkedin-mcp` (adapter `templates/external-app-repo/scripts/seal-app-secret.sh` de `k3s-homelab`) : `scripts/seal-secrets.sh`, qui génère les trois `SealedSecret` depuis les fichiers locaux existants (`linkedin_session.json`, `linkedin_mcp/tokens/`, `.env`).

**Ne jamais committer les fichiers Secret en clair** — seuls les `SealedSecret` résultants sont versionnés.

## Étape 6 — GitHub Actions

Depuis `linkedin-mcp` :

```bash
./scripts/setup-github-actions.sh
```

Enregistre le secret `BUILDKIT_HOST=tcp://buildkitd.cicd.svc.cluster.local:1234` sur le repo GitHub. Vérifier dans GitHub → Settings → Secrets and variables → Actions.

Le workflow généré (`.github/workflows/build-push.yml`, copié à l'étape 1) doit :
- builder sur `linux/amd64` via BuildKit distant
- pousser vers `registry.registry.svc.cluster.local:5000/linkedin-mcp:<sha>`
- committer le tag dans `kubernetes/deployment.yaml` (paths-ignore sur `kubernetes/**` pour éviter une boucle de déclenchement)

## Étape 7 — Premier déploiement et validation

```bash
cd ~/workspace/k3s-homelab
./scripts/preflight-app.sh linkedin-mcp
```

Puis push sur `linkedin-mcp` (branche `main`) pour déclencher le premier build. Vérifier :

```bash
export KUBECONFIG=~/.kube/config-k3s
kubectl get application -n argocd linkedin-mcp
kubectl get pods -n linkedin-mcp
kubectl logs -n linkedin-mcp deployment/linkedin-mcp --tail=50
```

Test de fumée en interne au cluster (pod éphémère) :

```bash
kubectl run -n linkedin-mcp curl-test --rm -it --restart=Never --image=curlimages/curl:8.5.0 -- \
  curl -sS http://linkedin-mcp.linkedin-mcp.svc:8000/mcp
```

## Étape 8 — Enregistrement dans LiteLLM

Dans `kubernetes/llm/litellm-configmap.yaml` (côté `k3s-homelab`), section `mcp_servers` :

```yaml
mcp_servers:
  linkedin-mcp:
    url: "http://linkedin-mcp.linkedin-mcp.svc:8000/mcp"
    transport: "streamable-http"
```

```bash
kubectl apply -f kubernetes/llm/litellm-configmap.yaml
kubectl rollout restart -n llm deployment/litellm
```

## Étape 9 — Virtual key scopée

Via l'UI LiteLLM (`llm.homelab`, admin uniquement) ou l'API `/key/generate` (Master Key, local) :

- Créer une virtual key dédiée `linkedin-mcp-key`
- Scope : accès **uniquement** au serveur `linkedin-mcp` (pas aux autres modèles/providers)
- **Décision à prendre avant génération** : tools autorisés au démarrage — proposition : commencer en lecture seule (`scrape_feed`, `scrape_post`, `get_posts`) et ajouter `like_post`/`repost_post`/`create_post` seulement après validation du flux complet, via `allowed_tools` / `mcp_tool_permissions`.

## Étape 10 — Validation de bout en bout

Appeler un tool en lecture seule via LiteLLM avec la nouvelle clé (client MCP pointant sur `https://llm.code-advisors.site/linkedin-mcp/mcp` avec le header d'auth de la virtual key) et vérifier :
- Réponse correcte (ex. `scrape_feed(count=1)`)
- Le Chromium distant reste sain (`curl http://192.168.1.153:9222/json/version`) et le pod `chromium-cdp-pod`/service `chromium-cdp-host` ne sont pas perturbés
- Pas de déclenchement de cooldown/rate-limit (`~/.cache/linkedin_scraper/cooldown.json` — mais attention, ce chemin est **local au pod**, pas partagé avec le Mac ; le cooldown persistant ADR-016 devient per-pod dans ce contexte, à noter comme limite connue)

## Notes / limites connues à surveiller

- **Cooldown et cache de permalien non partagés** : `~/.cache/linkedin_scraper/` (ADR-016) est un chemin local au filesystem du pod. Si le pod redémarre, le cooldown et le cache repartent à zéro. Envisager un volume persistant (`PVC`) monté sur ce chemin si ça devient un problème en usage réel.
- **Chromium partagé avec OpenClaw** : un pic d'usage simultané (scraping LinkedIn + OpenClaw actif) consomme les mêmes ressources CPU/RAM de `geekom-as6` — à surveiller si les deux usages deviennent fréquents (ADR-017, section Conséquences).
- **Vendoring `linkedin_scraper`** (étape 2) : à trancher avant d'écrire le Dockerfile définitif — impacte le workflow de mise à jour de la lib (aujourd'hui `editable`, donc un simple `git pull` suffit en local ; en image Docker il faudra un mécanisme de sync explicite).

## Ordre d'exécution recommandé

1. Trancher le vendoring `linkedin_scraper` (bloquant pour l'étape 2)
2. Étapes 1 → 7 (scaffolding + build + premier déploiement, sans encore brancher LiteLLM) — valider que le pod tourne et répond en interne
3. Étape 5 (secrets) doit être faite **avant** le premier déploiement réel (le pod ne démarrera pas sans la session LinkedIn)
4. Étapes 8 → 10 (branchement LiteLLM + clé + validation) une fois le pod confirmé sain de façon autonome
