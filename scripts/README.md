# Scripts — repo app

## `setup-github-actions.sh`

### À quoi ça sert

Le workflow **`.github/workflows/build-push.yml`** build l’image avec **BuildKit** qui tourne **dans ton cluster k3s** (service interne, pas sur les runners GitHub.com). Le job doit connaître l’adresse TCP de BuildKit : c’est le secret GitHub Actions **`BUILDKIT_HOST`**.

Sans ce secret (ou avec une mauvaise valeur), les étapes **buildx** / build restent en **erreur** ou **timeout** : le runner ARC dans le cluster ne sait pas où envoyer le build.

Ce script utilise la **GitHub CLI** (`gh`) pour :

1. **Authentification** (`gh auth login` si besoin).
2. **Optionnel** : **créer le dépôt** sur GitHub (`gh repo create`) si le dépôt n’existe pas encore — tu choisis **public** ou **privé**.
3. **`git init`** si le dossier n’est pas encore un dépôt git (sur demande).
4. **Commit initial** (ou proposition de commit) si nécessaire, puis **push** vers `origin`.
5. Enregistrer le secret **`BUILDKIT_HOST`** sur le dépôt (le dépôt **doit** exister côté GitHub pour cette étape).

### Quand l’utiliser

1. **Depuis la racine du clone** du repo app (là où se trouve `Dockerfile` / `kubernetes/`) :

   ```bash
   ./scripts/setup-github-actions.sh
   ```

2. Tu peux lancer ce script **sans avoir créé le dépôt à la main** sur github.com : le script proposera de le créer avec `gh` après avoir choisi la visibilité.

3. **Ré-exécution** : si le dépôt existe déjà et le secret est déjà là, tu peux relancer pour mettre à jour **`BUILDKIT_HOST`** ou après avoir révoqué un token.

### Prérequis

- **GitHub CLI** : `brew install gh`
- **git**
- Droits suffisants pour créer un dépôt sous **`owner/repo`** (compte perso ou org) et pour créer des **secrets Actions** sur ce dépôt.
- Le script déduit **`owner/repo`** dans cet ordre : variable d’environnement **`GH_REPO`**, sinon l’URL du remote **`origin`**, sinon la valeur par défaut issue de **`create-app.sh`** (`vinzlac/linkedin-mcp` substituée).

### Autres secrets / côté cluster

- **`BUILDKIT_HOST`** concerne **uniquement** le job GitHub Actions.
- Pour **tirer** l’image **GHCR privée** dans les pods, le cluster utilise un secret Kubernetes **`ghcr-pull`** (voir **README** à la racine et **`install-k3s.md`**).

## `seal-app-secret.sh`

### À quoi ça sert

Génère un **SealedSecret** dans **`kubernetes/<namespace>-<suffixe>.sealed.yaml`** : une variable à la fois, choisie parmi les clés listées dans **`.env.example`** à la racine du repo app (menu interactif ou **`--var`**). La valeur vient du **`.env`** (ou **direnv**) comme en local.

Exemples (namespace **`linkedin-mcp`**) :

- **`--var API_KEY`** → Secret **`linkedin-mcp-api`**, clé `API_KEY` (ingest / auth HTTP).
- **`--var LLM_API_KEY`** → Secret **`linkedin-mcp-llm`**, etc.

**`--nature`** permet de forcer le suffixe si le dérivé automatique ne convient pas.

### Prérequis

- **`.env.example`** (pour le menu / validation des noms) ;
- **`kubectl`** + **`kubeseal`** ;
- kubeconfig vers le cluster (Sealed Secrets installé).

### Mise à jour depuis le template homelab

Les fichiers **`README.md`** (racine) et **`scripts/README.md`** sont **réécrits** à chaque **`sync-app`**. Les scripts **`setup-github-actions.sh`** et **`seal-app-secret.sh`** sont **ajoutés** s’ils manquent. Si tu avais encore l’ancien **`seal-api-key.sh`**, supprime-le après **`sync-app`** : il est remplacé par **`seal-app-secret.sh --var API_KEY`**.
