FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY linkedin_mcp ./linkedin_mcp

RUN uv sync --frozen --no-dev

# Pas de `playwright install chromium` : connexion CDP au Chromium partagé du
# homelab (LINKEDIN_CDP_URL) — aucun navigateur local dans ce pod (ADR-017 de
# linkedin_scraper, docs/plan/deploy-k3s-argocd.md étape 2).

RUN useradd -u 1000 -m mcp && chown -R mcp:mcp /app
USER mcp

ENV MCP_TRANSPORT=streamable-http UV_NO_SYNC=1
EXPOSE 8000

CMD ["uv", "run", "linkedin-mcp"]
