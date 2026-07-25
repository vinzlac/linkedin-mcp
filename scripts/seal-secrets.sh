#!/usr/bin/env bash
# Scelle les 3 secrets nécessaires au déploiement k3s de linkedin-mcp (namespace linkedin-mcp),
# depuis les fichiers locaux existants (session Playwright + tokens OAuth + credentials OAuth) :
#   - linkedin-mcp-session       : linkedin_session.json (storage state Playwright)
#   - linkedin-mcp-oauth-tokens  : linkedin_mcp/tokens/*.json
#   - linkedin-mcp-oauth-creds   : LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET / LINKEDIN_REDIRECT_URI (depuis .env)
#
# Usage (racine du repo) : ./scripts/seal-secrets.sh
# Sortie : kubernetes/linkedin-mcp-{session,oauth-tokens,oauth-creds}.sealed.yaml
#
# Ne committe jamais les fichiers Secret en clair — seuls les *.sealed.yaml résultants le sont.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="linkedin-mcp"
K8S_REL="kubernetes"
CONTROLLER_NS="${SEALED_SECRETS_NAMESPACE:-sealed-secrets}"
CONTROLLER_NAME="${SEALED_SECRETS_CONTROLLER_NAME:-sealed-secrets}"

die() { echo "::error::$*" >&2; exit 1; }

command -v kubectl &>/dev/null || die "kubectl introuvable"
command -v kubeseal &>/dev/null || die "kubeseal introuvable — brew install kubeseal"

KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config-k3s}"
[[ "$KUBECONFIG" == ~* ]] && KUBECONFIG="${KUBECONFIG/#\~/$HOME}"
export KUBECONFIG

seal() {
  local name="$1"
  shift
  local out="${ROOT}/${K8S_REL}/${name}.sealed.yaml"
  kubectl create secret generic "$name" -n "$NAMESPACE" "$@" --dry-run=client -o yaml \
    | kubeseal -o yaml \
        --controller-namespace "$CONTROLLER_NS" \
        --controller-name "$CONTROLLER_NAME" \
    >"$out"
  echo "OK — ${out#$ROOT/}" >&2
}

# Chemins par défaut = ceux résolus par linkedin_mcp/config/settings.py en local (macOS/Linux).
SESSION_PATH="$(cd "$ROOT" && uv run python -c "from linkedin_mcp.config.settings import settings; print(settings.LINKEDIN_SESSION_PATH)")"
TOKENS_DIR="$(cd "$ROOT" && uv run python -c "from linkedin_mcp.config.settings import settings; print(settings.TOKEN_STORAGE_PATH)")"
[[ "$TOKENS_DIR" = /* ]] || TOKENS_DIR="${ROOT}/${TOKENS_DIR}"

# --- linkedin-mcp-session : storage state Playwright ---
[[ -f "$SESSION_PATH" ]] || die "Session introuvable : $SESSION_PATH — lance create_scrape_session (ou create_session.py) en local d'abord"
seal "linkedin-mcp-session" --from-file="linkedin_session.json=${SESSION_PATH}"

# --- linkedin-mcp-oauth-tokens : un fichier JSON par utilisateur ---
shopt -s nullglob
TOKEN_FILES=("$TOKENS_DIR"/*.json)
shopt -u nullglob
[[ ${#TOKEN_FILES[@]} -gt 0 ]] || die "Aucun token dans $TOKENS_DIR — lance authenticate en local d'abord"
TOKEN_ARGS=()
for f in "${TOKEN_FILES[@]}"; do
  TOKEN_ARGS+=(--from-file="$(basename "$f")=${f}")
done
seal "linkedin-mcp-oauth-tokens" "${TOKEN_ARGS[@]}"

# --- linkedin-mcp-oauth-creds : credentials OAuth (.env) ---
ENV_FILE="${ROOT}/.env"
[[ -f "$ENV_FILE" ]] || die ".env introuvable à la racine du repo ($ENV_FILE)"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
for v in LINKEDIN_CLIENT_ID LINKEDIN_CLIENT_SECRET LINKEDIN_REDIRECT_URI; do
  eval "[[ -n \"\${$v:-}\" ]]" || die "$v vide dans $ENV_FILE"
done
seal "linkedin-mcp-oauth-creds" \
  --from-literal="LINKEDIN_CLIENT_ID=${LINKEDIN_CLIENT_ID}" \
  --from-literal="LINKEDIN_CLIENT_SECRET=${LINKEDIN_CLIENT_SECRET}" \
  --from-literal="LINKEDIN_REDIRECT_URI=${LINKEDIN_REDIRECT_URI}"

echo "Terminé — committe les 3 fichiers ${K8S_REL}/linkedin-mcp-*.sealed.yaml (jamais le Secret en clair)." >&2
