#!/usr/bin/env bash
# Prépare ce dépôt pour GitHub Actions (homelab : BuildKit in-cluster).
# Peut créer le dépôt sur GitHub (gh), committer le squelette si besoin, pousser, puis enregistrer BUILDKIT_HOST.
#
# Prérequis : GitHub CLI — brew install gh ; git.
#
# Doc : scripts/README.md à la racine du repo app.
#
# Usage (à la racine de ce dépôt) :
#   ./scripts/setup-github-actions.sh
#
# Surcharges optionnelles :
#   BUILDKIT_HOST_VALUE=...   — défaut : tcp://buildkitd.cicd.svc.cluster.local:1234
#   GH_REPO=owner/repo        — défaut : déduit du remote origin, sinon vinzlac/linkedin-mcp (substitué par create-app)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "::error::$*" >&2; exit 1; }

BUILDKIT_HOST_VALUE="${BUILDKIT_HOST_VALUE:-tcp://buildkitd.cicd.svc.cluster.local:1234}"
# Valeur par défaut après create-app.sh (substitution) :
DEFAULT_REPO_SPEC="vinzlac/linkedin-mcp"

if ! command -v gh &>/dev/null; then
  echo "GitHub CLI (gh) introuvable — installe : brew install gh" >&2
  exit 1
fi

if ! command -v git &>/dev/null; then
  echo "git introuvable" >&2
  exit 1
fi

if ! gh auth status &>/dev/null; then
  echo ">>> Aucune session gh — lancement de gh auth login (navigateur ou token)"
  gh auth login
fi

if ! gh auth status &>/dev/null; then
  echo "Authentification gh requise — abandon." >&2
  exit 1
fi

# owner/repo depuis une URL GitHub (https ou ssh).
parse_github_remote() {
  local u="${1%.git}"
  u="${u#https://}"
  u="${u#http://}"
  if [[ "$u" =~ ^git@github\.com:([^/]+)/(.+)$ ]]; then
    echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    return 0
  fi
  if [[ "$u" =~ ^github\.com/([^/]+)/(.+)$ ]]; then
    echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    return 0
  fi
  return 1
}

resolve_repo_spec() {
  if [[ -n "${GH_REPO:-}" ]]; then
    echo "$GH_REPO"
    return
  fi
  if [[ -d .git ]] && url="$(git remote get-url origin 2>/dev/null)"; then
    if spec="$(parse_github_remote "$url")"; then
      echo "$spec"
      return
    fi
  fi
  echo "$DEFAULT_REPO_SPEC"
}

github_repo_exists() {
  gh repo view "$1" &>/dev/null
}

ensure_git_init() {
  if [[ -d .git ]]; then
    return 0
  fi
  echo "Aucun dépôt git dans : $ROOT"
  read -r -p "Lancer « git init » (branche main) ici ? [o/N] " a || true
  [[ "${a:-}" =~ ^[oOyY]$ ]] || die "Sans git local, abandon. Ex. : git init -b main && git add . && git commit -m \"chore: initial\""
  git init -b main
  echo ">>> git init OK"
}

ensure_initial_commit() {
  if git rev-parse -q --verify HEAD >/dev/null 2>&1; then
    return 0
  fi
  if [[ -z "$(git ls-files 2>/dev/null)" ]] && [[ ! -e .gitignore ]]; then
    die "Rien à versionner (répertoire vide ?). Ajoute les fichiers du squelette puis relance."
  fi
  git add -A
  if [[ -z "$(git status --porcelain)" ]]; then
    die "Aucun fichier suivi à committer."
  fi
  git commit -m "chore: initial commit (squelette homelab)"
  echo ">>> Commit initial créé."
}

maybe_commit_dirty() {
  [[ -d .git ]] || return 0
  git rev-parse -q --verify HEAD >/dev/null 2>&1 || return 0
  [[ -n "$(git status --porcelain)" ]] || return 0
  echo "Des fichiers ne sont pas commités."
  read -r -p "Créer un commit « chore: sync avant setup GitHub » ? [o/N] " c || true
  if [[ "${c:-}" =~ ^[oOyY]$ ]]; then
    git add -A
    git commit -m "chore: sync avant setup GitHub"
    echo ">>> Commit créé."
  fi
}

remove_origin_if_wrong() {
  local want="$1"
  local cur url spec
  url="$(git remote get-url origin 2>/dev/null)" || return 0
  spec="$(parse_github_remote "$url")" || {
    echo "(!) origin existe avec une URL non reconnue comme github.com : $url"
    read -r -p "Supprimer le remote « origin » pour continuer ? [o/N] " o || true
    [[ "${o:-}" =~ ^[oOyY]$ ]] || die "Annulé."
    git remote remove origin
    return 0
  }
  if [[ "$spec" != "$want" ]]; then
    echo "(!) origin → $spec, attendu $want"
    read -r -p "Remplacer origin par https://github.com/${want}.git ? [o/N] " o || true
    [[ "${o:-}" =~ ^[oOyY]$ ]] || die "Annulé."
    git remote remove origin
  fi
}

ensure_origin_for_existing_repo() {
  local spec="$1"
  if git remote get-url origin &>/dev/null; then
    remove_origin_if_wrong "$spec"
  fi
  if ! git remote get-url origin &>/dev/null; then
    local url
    url="$(gh repo view "$spec" --json url -q .url 2>/dev/null)" || url="https://github.com/${spec}"
    git remote add origin "$url"
    echo ">>> Remote origin ajouté : $url"
  fi
}

push_current_branch() {
  local br
  br="$(git branch --show-current 2>/dev/null || true)"
  [[ -n "$br" ]] || die "Branche courante introuvable (detached HEAD ?). Passe sur main puis relance."
  git push -u origin "$br"
  echo ">>> Poussé : origin/$br"
}

REPO_SPEC="$(resolve_repo_spec)"
if [[ "$REPO_SPEC" == *"__"* ]]; then
  die "Nom de dépôt GitHub indéterminé (placeholder non substitué). Utilise : GH_REPO=owner/repo ou configure git remote origin."
fi

ensure_git_init

echo ""
echo "Dépôt GitHub cible : ${REPO_SPEC}"
echo ""

if github_repo_exists "$REPO_SPEC"; then
  echo ">>> Le dépôt distant existe déjà sur GitHub."
  ensure_origin_for_existing_repo "$REPO_SPEC"
  if ! git rev-parse -q --verify HEAD >/dev/null 2>&1; then
    ensure_initial_commit
  else
    maybe_commit_dirty
  fi
  read -r -p "Pousser la branche courante vers origin (requis si le distant est vide) ? [O/n] " ps || true
  if [[ -z "${ps:-}" || "${ps:-}" =~ ^[oOyY]$ ]]; then
    push_current_branch || {
      echo "(!) git push a échoué — résous les conflits (pull/rebase) puis relance ce script pour BUILDKIT_HOST uniquement."
      exit 1
    }
  fi
else
  echo "Le dépôt « ${REPO_SPEC} » n’existe pas encore sur GitHub (ou tu n’y as pas accès)."
  read -r -p "Créer le dépôt avec gh, committer si besoin, et pousser ? [O/n] " cr || true
  if [[ -n "${cr:-}" && ! "${cr:-}" =~ ^[oOyY]$ ]]; then
    die "Crée le dépôt sur GitHub manuellement, pousse les commits, puis relance : ./scripts/setup-github-actions.sh"
  fi

  echo "Visibilité du nouveau dépôt :"
  echo "  1) public"
  echo "  2) privé"
  read -r -p "Choix [1/2] (défaut 2) : " vis || true
  VIS_FLAG=(--private)
  [[ "${vis:-2}" == "1" ]] && VIS_FLAG=(--public)

  ensure_initial_commit
  maybe_commit_dirty

  remove_origin_if_wrong "$REPO_SPEC"

  if git remote get-url origin &>/dev/null; then
    echo ">>> Création du dépôt distant (origin déjà présent localement) puis push"
    gh repo create "$REPO_SPEC" "${VIS_FLAG[@]}" --description "Homelab k3s app"
    push_current_branch
  else
    echo ">>> gh repo create (source = ., remote=origin, --push)"
    gh repo create "$REPO_SPEC" "${VIS_FLAG[@]}" --source=. --remote=origin --push --description "Homelab k3s app"
  fi
  echo ">>> Dépôt créé et squelette poussé."
fi

echo ""
echo ">>> gh secret set BUILDKIT_HOST sur ${REPO_SPEC}"
gh secret set BUILDKIT_HOST --body "$BUILDKIT_HOST_VALUE" --repo "$REPO_SPEC"

echo ""
echo "OK — ${REPO_SPEC} → Settings → Secrets and variables → Actions → BUILDKIT_HOST"
echo "    Valeur : ${BUILDKIT_HOST_VALUE}"
