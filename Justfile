# linkedin-mcp — task runner
# Requires: uv, just

default:
    @just --list

# Crée le fichier de session LinkedIn (login manuel dans le navigateur)
session:
    uv run python create_session.py

# Lance le serveur MCP LinkedIn
run:
    uv run linkedin-mcp

# Teste la création d'un post LinkedIn
test-post TEXT="Test de post":
    uv run python test_create_post.py "{{ TEXT }}"

# Teste le scraping du feed LinkedIn
test-feed N="5":
    uv run python test_scrape_feed.py {{ N }}
