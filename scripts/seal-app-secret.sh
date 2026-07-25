#!/usr/bin/env bash
# SealedSecret pour cette app (namespace linkedin-mcp) : une variable à la fois.
# Le nom du Secret est « linkedin-mcp-<suffixe> » ; le suffixe est dérivé du nom de la variable
# (ex. API_KEY → api → linkedin-mcp-api ; LLM_API_KEY → llm → linkedin-mcp-llm), sauf si --nature est fourni.
#
# Les variables proposées en interactif sont listées depuis « .env.example » à la racine du repo app.
# La valeur est lue dans « .env » (direnv, ou chargement auto si la variable est encore vide).
#
# Usage (racine du repo app) :
#   ./scripts/seal-app-secret.sh --var API_KEY
#   ./scripts/seal-app-secret.sh --var LLM_API_KEY
#   ./scripts/seal-app-secret.sh --var QDRANT_API_KEY --nature qdrant
#   ./scripts/seal-app-secret.sh -o kubernetes/custom.sealed.yaml --var FOO
#   ./scripts/seal-app-secret.sh   # TTY : menu des clés .env.example
#
# Sortie par défaut : kubernetes/<namespace>-<suffixe>.sealed.yaml
#
# Surcharges :
#   SEAL_APP_ENV_FILE   — fichier .env à charger (défaut : racine du repo)
#   SEALED_SECRETS_NAMESPACE / SEALED_SECRETS_CONTROLLER_NAME
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="linkedin-mcp"
APP_K8S_LABEL="linkedin-mcp"
K8S_REL="kubernetes"
CONTROLLER_NS="${SEALED_SECRETS_NAMESPACE:-sealed-secrets}"
CONTROLLER_NAME="${SEALED_SECRETS_CONTROLLER_NAME:-sealed-secrets}"

die() { echo "::error::$*" >&2; exit 1; }

list_keys_from_app_env_example() {
  local f="$ROOT/.env.example"
  [[ -f "$f" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]]; then
      echo "${BASH_REMATCH[1]}"
    elif [[ "$line" =~ ^#[[:space:]]*([A-Z][A-Z0-9_]*)= ]]; then
      echo "${BASH_REMATCH[1]}"
    fi
  done <"$f" | sort -u
}

validate_nature_slug() {
  local n="$1"
  [[ "$n" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || die "Suffixe / nature invalide « $n » (minuscules, chiffres, tirets)"
}

# Suffixe Secret : API_KEY → api ; FOO_API_KEY → foo ; autre → nom en minuscules avec _ → -
derive_suffix_from_var() {
  local v="$1"
  if [[ "$v" == "API_KEY" ]]; then
    echo "api"
  elif [[ "$v" =~ ^([A-Z0-9]+)_API_KEY$ ]]; then
    echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]'
  else
    echo "$v" | tr '[:upper:]_' '[:lower:]-'
  fi
}

usage() {
  cat <<USAGE
Usage : ./scripts/seal-app-secret.sh [--var NOM] [--nature slug] [-o fichier] [--dry-secret]
  App : namespace ${NAMESPACE} (${APP_K8S_LABEL})
  --var   variable (doit figurer dans .env.example si ce fichier existe)
  --nature  forcer le suffixe du Secret (défaut : dérivé du nom de variable)
  -o      fichier de sortie (défaut : ${K8S_REL}/${NAMESPACE}-<suffixe>.sealed.yaml)
  Sans arg + TTY : menu interactif (clés depuis .env.example)
USAGE
}

ENV_VAR=""
NATURE=""
OUT=""
DRY_SECRET=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --var) ENV_VAR="$2"; shift 2 ;;
    --nature) NATURE="$2"; shift 2 ;;
    -o) OUT="$2"; shift 2 ;;
    --dry-secret) DRY_SECRET=1; shift ;;
    *) die "Option inconnue : $1" ;;
  esac
done

pick_var_interactive() {
  [[ -f "$ROOT/.env.example" ]] || die ".env.example introuvable à la racine du repo — crée-le ou utilise --var"
  mapfile -t KEYS < <(list_keys_from_app_env_example)
  [[ ${#KEYS[@]} -gt 0 ]] || die "Aucune clé dans .env.example"
  echo "=== Variables (.env.example) — app ${APP_K8S_LABEL} ==="
  local i=1 k
  for k in "${KEYS[@]}"; do
    echo "  ${i}) ${k}"
    ((i++)) || true
  done
  echo ""
  read -r -p "Numéro ou nom exact : " CHOICE || true
  CHOICE="${CHOICE//[[:space:]]/}"
  [[ -n "$CHOICE" ]] || die "Choix vide"
  if [[ "$CHOICE" =~ ^[0-9]+$ ]]; then
    local idx=$((CHOICE - 1))
    (( idx >= 0 && idx < ${#KEYS[@]} )) || die "Numéro invalide"
    ENV_VAR="${KEYS[$idx]}"
  else
    local found=0
    for k in "${KEYS[@]}"; do
      [[ "$k" == "$CHOICE" ]] && ENV_VAR="$k" && found=1 && break
    done
    [[ "$found" -eq 1 ]] || die "Inconnu dans .env.example : $CHOICE"
  fi
}

if [[ -z "$ENV_VAR" ]]; then
  [[ -t 0 ]] || die "Sans TTY : fournir --var"
  pick_var_interactive
else
  if list_keys_from_app_env_example &>/dev/null; then
    mapfile -t KEYS < <(list_keys_from_app_env_example)
    ok=0
    for k in "${KEYS[@]}"; do
      [[ "$k" == "$ENV_VAR" ]] && ok=1 && break
    done
    [[ "$ok" -eq 1 ]] || die "« $ENV_VAR » absent de .env.example — ajoute-la ou corrige"
  fi
fi

[[ "$ENV_VAR" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "Nom de variable invalide : $ENV_VAR"

if [[ -z "$NATURE" ]]; then
  NATURE="$(derive_suffix_from_var "$ENV_VAR")"
fi
NATURE="$(echo "$NATURE" | tr '[:upper:]' '[:lower:]')"
validate_nature_slug "$NATURE"

SECRET_NAME="${NAMESPACE}-${NATURE}"

if [[ -z "$OUT" ]]; then
  OUT="${ROOT}/${K8S_REL}/${SECRET_NAME}.sealed.yaml"
fi

ENV_FILE="${SEAL_APP_ENV_FILE:-$ROOT/.env}"
eval 'VAL="${'"$ENV_VAR"':-}"'
if [[ -z "$VAL" && -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  eval 'VAL="${'"$ENV_VAR"':-}"'
fi
if [[ -z "$VAL" ]]; then
  die "« $ENV_VAR » vide — exporte-la ou mets-la dans $ENV_FILE (direnv / .env)"
fi

KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config-k3s}"
[[ "$KUBECONFIG" == ~* ]] && KUBECONFIG="${KUBECONFIG/#\~/$HOME}"
export KUBECONFIG

emit_plain() {
  kubectl create secret generic "$SECRET_NAME" -n "$NAMESPACE" \
    --from-literal="${ENV_VAR}=${VAL}" \
    --dry-run=client -o yaml
}

if [[ -n "${DRY_SECRET:-}" ]]; then
  emit_plain
  exit 0
fi

command -v kubectl &>/dev/null || die "kubectl introuvable"
command -v kubeseal &>/dev/null || die "kubeseal introuvable — brew install kubeseal"

mkdir -p "$(dirname "$OUT")"
emit_plain | kubeseal -o yaml \
  --controller-namespace "$CONTROLLER_NS" \
  --controller-name "$CONTROLLER_NAME" \
  >"$OUT"

echo "OK — $OUT" >&2
echo "    Secret ${SECRET_NAME} (clé ${ENV_VAR}) — namespace ${NAMESPACE} — à committer (pas le .env en clair)" >&2
