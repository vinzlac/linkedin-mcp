# Contexte homelab — k3s, CI/CD et ce dépôt

> **Objectif** : document de référence pour un **assistant IA** ou un nouveau contributeur travaillant **dans ce dépôt app** (hors mono-repo `k3s-homelab`). Dernière génération / mise à jour : **2026-07-25T18:01:54Z**.

---

## 1. Où tourne la prod ?

| Élément | Valeur |
|---------|--------|
| **Machine** | geekom-as6 (mini-PC homelab) |
| **IP LAN typique** | 192.168.1.153 |
| **Orchestrateur** | **k3s** (Kubernetes léger), conteneurs via **containerd** |
| **Données volumineuses** | Sous **`/data`** sur le serveur (k3s, volumes, etc.) |

Accès admin : **SSH** vers `geekom-as6` (clé SSH, utilisateur défini dans l’inventaire Ansible du repo homelab).

---

## 2. CI — build image (ce dépôt)

Les workflows GitHub Actions tournent sur des runners **ARC** (*Actions Runner Controller*) **dans le cluster** (pods éphémères), pas sur les runners hébergés par GitHub.com.

| Paramètre | Détail |
|-----------|--------|
| **Label workflow** | `runs-on: arc-runner-linkedin-mcp` (nom du *scale set* Helm = nom d’installation ARC) |
| **Build** | **BuildKit** en **ClusterIP** dans le namespace `cicd` |
| **Secret obligatoire** | **`BUILDKIT_HOST`** = `tcp://buildkitd.cicd.svc.cluster.local:1234` |
| **Registry** | **GHCR** — image **`ghcr.io/vinzlac/linkedin-mcp`** (tags **`:<sha>`** et **`:main`**) |
| **Déclencheur** | Push sur **`main`** ; les commits qui ne touchent que **`kubernetes/`** ne relancent pas le build (`paths-ignore`) pour éviter une boucle avec le commit bot sur `deployment.yaml` |
| **GitOps dans ce repo** | Après build, le workflow met à jour **`kubernetes/deployment.yaml`** (ligne `image:` avec le SHA) via `github-actions[bot]` (`contents: write` + `packages: write`) |

Plateforme image cible : **linux/amd64** (nœud homelab type Intel/AMD).

---

## 3. CD — Argo CD (GitOps)

Argo CD est installé **sur le cluster** ; il lit les manifests **dans ce dépôt Git** (pas dans le repo `k3s-homelab` pour le code app).

| Paramètre | Valeur |
|-----------|--------|
| **Dépôt source (celui-ci)** | https://github.com/vinzlac/linkedin-mcp.git |
| **Branche** | main |
| **Chemin manifests** | `kubernetes/` |
| **Namespace cible** | **`linkedin-mcp`** |
| **Application Argo (nom)** | **`linkedin-mcp`** |
| **Déclaration côté pilote** | Fichier Application dans le repo **`https://github.com/vinzlac/k3s-homelab.git`** : `kubernetes/argocd/applications/linkedin-mcp.yaml` |
| **Sync** | Automatisée (polling ~3 min, pas de webhook Internet obligatoire) |

Si ce dépôt GitHub est **privé**, le cluster Argo utilise un **PAT** + secret **`repo-creds`** (préfixe d’URL) — voir la doc homelab **ADR-0022**.

---

## 4. Accès application (interne au cluster uniquement)

| Élément | Valeur |
|---------|--------|
| **Ingress** | Aucun — pas d'exposition LAN/Internet directe |
| **Service** | `ClusterIP`, `linkedin-mcp.linkedin-mcp.svc:8000` |
| **Exposition** | Via le MCP Gateway de LiteLLM (`https://llm.code-advisors.site`), déjà exposé publiquement — voir `docs/plan/deploy-k3s-argocd.md` étapes 8-9 |

**Secret pull GHCR** (si le package image est **privé**) : secret **`ghcr-pull`** dans le namespace **`linkedin-mcp`**, référencé par le Deployment (`imagePullSecrets`).

---

## 5. Référence dépôt « pilote » (infra)

Toute la doc vivante (Ansible, manifests cluster, scripts, ADR) est dans :

- **`https://github.com/vinzlac/k3s-homelab.git`** (clone local habituel : dossier **voisin** de ce repo, ex. `../k3s-homelab`)

Liens utiles (chemins dans ce repo) :

- ARC + BuildKit : `docs/guides/install-arc-k3s.md`, `docs/plan/plan-cicd-buildkit-todoapp.md`
- Argo CD : `docs/plan/plan-argocd-k3s.md`, `kubernetes/argocd/README.md`
- Ajouter une app externe : `docs/guides/guide-add-external-app-k3s.md`

Scripts typiques depuis une machine avec **kubectl** configuré :

- Kubeconfig : `scripts/setup-kubeconfig.sh` → `~/.kube/config-k3s`
- Vérifier les pods : `scripts/check-pods-running.sh`

---

## 6. Récap technique (ce dépôt)

| Clé | Valeur |
|-----|--------|
| **Org / repo GitHub** | vinzlac / **linkedin-mcp** |
| **Ressource K8s (Deployment/Service)** | linkedin-mcp |
| **Image** | `ghcr.io/vinzlac/linkedin-mcp:<sha>` (mis à jour par CI) |
| **Stack (create-app)** | Dépôt GitHub déjà existant — pas de squelette (CI/k8s : sync-app ou merge manuel) (`existing-repo`) |

Les détails (ports, resources, probes, `securityContext`) suivent **`Dockerfile`** et **`kubernetes/deployment.yaml`** dans **ce** dépôt.

---

*Fichier généré ou régénéré par **`k3s-homelab/scripts/create-app.sh`** ou **`update-app.sh`** — à committer dans ce dépôt app pour que les outils IA aient le contexte.*
