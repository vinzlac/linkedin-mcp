#!/usr/bin/env python3
"""Test script: authenticate + create a LinkedIn post.

Usage:
    uv run python test_create_post.py "Mon texte de post"
    uv run python test_create_post.py  # utilise le texte par défaut
"""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.config.settings import settings
from linkedin_mcp.linkedin.auth import LinkedInOAuth
from linkedin_mcp.linkedin.post import PostManager, PostRequest

PORT = 3000
DEFAULT_TEXT = "Test de post avant suppression"


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        self.server.auth_code = params.get("code", [None])[0]
        self.server.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Auth OK - tu peux fermer cet onglet.</h1>")
        self.server.auth_received.set()

    def log_message(self, *_):
        pass


def _start_callback_server() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", PORT), _CallbackHandler)
    server.auth_code = None
    server.state = None
    server.auth_received = threading.Event()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _find_existing_token() -> str | None:
    token_dir = settings.TOKEN_STORAGE_PATH
    if not os.path.isdir(token_dir):
        return None
    for filename in os.listdir(token_dir):
        if filename.endswith(".json"):
            return filename[:-5]
    return None


async def authenticate(auth_client: LinkedInOAuth) -> None:
    import webbrowser

    print("🔐 Démarrage du flow OAuth2...")
    server = _start_callback_server()
    print(f"   Serveur callback démarré sur 127.0.0.1:{PORT}")

    auth_url, expected_state = await auth_client.get_authorization_url()
    print("   Ouverture du navigateur...")
    if not webbrowser.open(auth_url):
        print(f"   ⚠️  Ouvre manuellement cette URL :\n   {auth_url}")

    print("   En attente du callback LinkedIn (120s max)...")
    received = await asyncio.get_event_loop().run_in_executor(
        None, lambda: server.auth_received.wait(120)
    )
    server.shutdown()

    if not received or not server.auth_code or server.state != expected_state:
        raise RuntimeError(
            f"Callback invalide (code={server.auth_code is not None}, "
            f"state_match={server.state == expected_state})"
        )

    await auth_client.exchange_code(server.auth_code)
    user_info = await auth_client.get_user_info()
    auth_client.save_tokens(user_info.sub)
    print(f"✅ Authentifié : {user_info.name} ({user_info.sub})")


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEXT

    auth_client = LinkedInOAuth()

    user_id = _find_existing_token()
    if user_id:
        if auth_client.load_tokens(user_id):
            await auth_client.get_user_info()
            print(f"✅ Token existant chargé pour : {user_id}")
        else:
            print("⚠️  Token corrompu, relance de l'auth...")
            await authenticate(auth_client)
    else:
        await authenticate(auth_client)

    manager = PostManager(auth_client)
    print(f"\n📝 Création du post : \"{text}\"")
    post_id = await manager.create_post(PostRequest(text=text))
    print(f"✅ Post créé avec succès ! ID : {post_id}")


if __name__ == "__main__":
    asyncio.run(main())
