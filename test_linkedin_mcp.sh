#!/bin/bash
# Test du serveur MCP LinkedIn en local
# Simule exactement ce que Claude Desktop envoie

set -e

WORKSPACE="/Users/vinz/workspace/linkedin-mcp"
ENV_FILE="$WORKSPACE/.env"

# Couleurs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=============================="
echo "  Test MCP LinkedIn Server"
echo "=============================="
echo ""

# Vérifie que le .env existe
if [ ! -f "$ENV_FILE" ]; then
  echo -e "${RED}❌ Fichier .env introuvable : $ENV_FILE${NC}"
  exit 1
fi
echo -e "${GREEN}✅ .env trouvé${NC}"

# Charge les variables
export $(grep -v '^#' "$ENV_FILE" | xargs)

# Vérifie les variables
if [ -z "$LINKEDIN_CLIENT_ID" ] || [ -z "$LINKEDIN_CLIENT_SECRET" ] || [ -z "$LINKEDIN_REDIRECT_URI" ]; then
  echo -e "${RED}❌ Variables manquantes dans .env${NC}"
  exit 1
fi
echo -e "${GREEN}✅ Credentials OK (CLIENT_ID: $LINKEDIN_CLIENT_ID)${NC}"

# Message initialize complet (comme Claude Desktop)
INIT_MSG='{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}},"id":1}'
TOOLS_MSG='{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'

echo ""
echo -e "${YELLOW}▶ Démarrage du serveur MCP...${NC}"
echo ""

# Lance le serveur avec les deux messages et attend 3 secondes
RESPONSE=$(cd "$WORKSPACE" && printf '%s\n%s\n' "$INIT_MSG" "$TOOLS_MSG" | \
  timeout 5 uv run python -c "from linkedin_mcp.server import main; main()" 2>/dev/null || true)

if echo "$RESPONSE" | grep -q '"result"'; then
  echo -e "${GREEN}✅ Serveur répond correctement !${NC}"
  echo ""
  echo "--- Réponse initialize ---"
  echo "$RESPONSE" | head -1 | python3 -m json.tool 2>/dev/null || echo "$RESPONSE" | head -1
  echo ""
  echo "--- Outils disponibles ---"
  echo "$RESPONSE" | tail -1 | python3 -m json.tool 2>/dev/null || echo "$RESPONSE" | tail -1
else
  echo -e "${RED}❌ Pas de réponse JSON valide${NC}"
  echo "Output reçu : $RESPONSE"
  exit 1
fi

echo ""
echo -e "${GREEN}=============================="
echo -e "  ✅ Serveur MCP opérationnel"
echo -e "==============================${NC}"
echo ""
echo "Tu peux maintenant redémarrer Claude Desktop !"