#!/usr/bin/env python3
"""Test d'intégration MCP : scrape_post sur une URL LinkedIn connue.

Vérifie que l'outil scrape_post est exposé et peut lire un post réel
via le protocole MCP (initialize → tools/list → tools/call).

Usage:
    uv run python test_mcp_scrape_post.py
    uv run python test_mcp_scrape_post.py "<post_url>"
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from linkedin_mcp.config.settings import settings

DEFAULT_POST_URL = (
    "https://www.linkedin.com/posts/nmartignole_je-rentre-de-2-jours-de-conf"
    "%C3%A9rences-passionnants-share-7474414301475213312-8xjU/"
    "?utm_source=share&utm_medium=member_desktop"
    "&rcm=ACoAAAEKNzwB4E69dZJoooWDrlDaPEU8Mpaezoc"
)

INIT_MSG = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test-scrape-post", "version": "1.0.0"},
    },
    "id": 1,
}

INITIALIZED_MSG = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {},
}

TOOLS_MSG = {
    "jsonrpc": "2.0",
    "method": "tools/list",
    "params": {},
    "id": 2,
}

SERVER_CMD = [
    "uv",
    "run",
    "python",
    "-u",
    "-c",
    "from linkedin_mcp.server import main; main()",
]


def _read_until_id(proc: subprocess.Popen[str], target_id: int) -> dict[str, Any]:
    """Lit stdout ligne par ligne jusqu'à recevoir une réponse avec target_id."""
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"Flux MCP fermé avant réponse id={target_id}")
        line = line.strip()
        if not line.startswith("{"):
            continue
        message = json.loads(line)
        if message.get("id") == target_id:
            return message


def _mcp_session() -> subprocess.Popen[str]:
    return subprocess.Popen(
        SERVER_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _call_mcp_tool(name: str, arguments: dict[str, Any], msg_id: int, timeout: int) -> dict[str, Any]:
    """Handshake MCP puis tools/call ; stdin reste ouvert pendant l'exécution."""
    proc = _mcp_session()
    assert proc.stdin is not None
    try:
        proc.stdin.write(json.dumps(INIT_MSG) + "\n")
        proc.stdin.flush()
        init_resp = _read_until_id(proc, 1)
        if "error" in init_resp:
            raise RuntimeError(f"initialize a échoué : {init_resp['error']}")

        proc.stdin.write(json.dumps(INITIALIZED_MSG) + "\n")
        proc.stdin.flush()

        call_msg = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": msg_id,
        }
        proc.stdin.write(json.dumps(call_msg) + "\n")
        proc.stdin.flush()

        # Ne pas fermer stdin : le serveur doit rester vivant pendant le scraping.
        return _read_until_id(proc, msg_id)
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()


def _list_tools() -> list[str]:
    """tools/list via batch stdin (rapide, pas de handshake séquentiel nécessaire)."""
    payload = "\n".join(json.dumps(m) for m in (INIT_MSG, TOOLS_MSG)) + "\n"
    process = subprocess.run(
        SERVER_CMD,
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    for line in process.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        message = json.loads(line)
        if message.get("id") == 2:
            tools = message.get("result", {}).get("tools", [])
            return [t.get("name", "") for t in tools if isinstance(t, dict)]
    raise RuntimeError("tools/list : réponse introuvable")


def main() -> int:
    post_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_POST_URL
    session_path = Path(settings.LINKEDIN_SESSION_PATH).expanduser()

    print("=== Test MCP scrape_post ===\n", flush=True)

    if not session_path.exists():
        print(f"❌ Session Playwright introuvable : {session_path}")
        print("   Lance create_scrape_session ou `uv run python create_session.py`")
        return 1
    print(f"✅ Session trouvée : {session_path}")

    print("\n▶ tools/list …", flush=True)
    try:
        tool_names = _list_tools()
    except Exception as exc:
        print(f"❌ tools/list a échoué : {exc}")
        return 1

    if "scrape_post" not in tool_names:
        print(f"❌ Outil scrape_post absent. Outils : {', '.join(tool_names)}")
        return 1
    print(f"✅ scrape_post présent ({len(tool_names)} outils au total)")

    print(f"\n▶ tools/call scrape_post …\n   {post_url[:80]}…", flush=True)
    print("   (peut prendre 30–90 s : ouverture Chromium + navigation)\n", flush=True)

    try:
        call_response = _call_mcp_tool(
            "scrape_post",
            {"post_url": post_url},
            msg_id=3,
            timeout=30,
        )
    except Exception as exc:
        print(f"❌ tools/call a échoué : {exc}")
        return 1

    if "error" in call_response:
        print("❌ tools/call a renvoyé une erreur :")
        print(json.dumps(call_response["error"], indent=2, ensure_ascii=False))
        return 1

    content = call_response.get("result", {}).get("content", [])
    if not content:
        print("❌ Réponse vide (pas de content)")
        return 1

    text_blocks = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    raw = "\n".join(text_blocks).strip()
    if call_response.get("result", {}).get("isError"):
        print(f"❌ tools/call isError : {raw[:500]}")
        return 1
    if not raw or "Aucun post trouvé" in raw:
        print(f"❌ Post non récupéré : {raw[:200]}")
        return 1

    try:
        posts = json.loads(raw)
    except json.JSONDecodeError:
        print("❌ Contenu non JSON :")
        print(raw[:500])
        return 1

    if not isinstance(posts, list) or not posts:
        print("❌ Liste de posts vide")
        return 1

    post = posts[0]
    author = (post.get("author_name") or "").lower()
    text = (post.get("text") or "").lower()

    checks = [
        ("texte Voxxed", "voxxed" in text),
        (
            "contenu du post",
            "agentique" in text
            or "météorite" in text
            or "dinosaures" in text
            or "conférences" in text,
        ),
        (
            "engagement",
            (post.get("reactions_count") or 0) >= 50
            or (post.get("comments_count") or 0) >= 10,
        ),
    ]
    failed = [label for label, ok in checks if not ok]

    author_display = post.get("author_name") or "?"
    preview = (post.get("text") or "")[:100].replace("\n", " ")
    print(f"✅ Post lu : {author_display}")
    print(f"   {preview}…")
    print(f"   👍 {post.get('reactions_count')}  💬 {post.get('comments_count')}")
    print(f"   🔗 {post.get('linkedin_url')}")

    if "martignole" not in author and "martignole" not in text:
        print(
            "\nℹ️  Auteur DOM ≠ Nicolas Martignole (souvent la session active sur page détail) "
            "— le contenu du post est néanmoins extrait."
        )

    if failed:
        print(f"\n⚠️  Vérifications partielles échouées : {', '.join(failed)}")
        print("   Le post a été lu mais le contenu ne correspond pas exactement à l'attendu.")
        return 1

    print("\n✅ Test scrape_post réussi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
