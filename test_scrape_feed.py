#!/usr/bin/env python3
"""Test script: scrape LinkedIn feed posts via Playwright session.

Usage:
    uv run python test_scrape_feed.py              # 5 posts, JSON → output/feed.json
    uv run python test_scrape_feed.py 10           # 10 posts → output/feed.json
    uv run python test_scrape_feed.py 10 -         # pas de fichier (affichage seulement)
    uv run python test_scrape_feed.py 3 chemin.json  # autre fichier
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.config.settings import settings
from linkedin_scraper import BrowserManager, FeedScraper

DEFAULT_COUNT = 5
REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_FEED_JSON = REPO_ROOT / "output" / "feed.json"


async def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COUNT
    out_arg = sys.argv[2] if len(sys.argv) > 2 else None
    if out_arg == "-":
        output_file: Path | None = None
    elif out_arg:
        output_file = Path(out_arg)
    else:
        output_file = DEFAULT_FEED_JSON

    session_path = settings.LINKEDIN_SESSION_PATH
    if not session_path or not Path(session_path).exists():
        print(f"❌ Fichier de session introuvable : {session_path}")
        print("   Génère-le avec : cd ../linkedin_scraper && just session")
        sys.exit(1)

    print(f"🌐 Initialisation du navigateur (session : {session_path})...")
    browser = BrowserManager(headless=False)
    await browser.start()
    await browser.load_session(session_path)
    print("✅ Session chargée")

    try:
        print(f"\n📰 Scraping de {count} posts du feed LinkedIn...")
        scraper = FeedScraper(browser.page)
        posts = await scraper.scrape(limit=count)

        if not posts:
            print("⚠️  Aucun post trouvé dans le feed.")
            return

        print(f"✅ {len(posts)} post(s) récupéré(s)\n")

        data = [p.model_dump() for p in posts]

        for i, post in enumerate(data, 1):
            author = post.get("author_name") or "?"
            text_preview = (post.get("text") or "")[:80].replace("\n", " ")
            date = post.get("posted_date") or "?"
            reactions = post.get("reactions_count")
            comments = post.get("comments_count")
            print(f"[{i}] {author} · {date}")
            print(f"    {text_preview}{'…' if len(post.get('text') or '') > 80 else ''}")
            print(f"    👍 {reactions}  💬 {comments}")
            print()

        if output_file:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"💾 Résultats sauvegardés dans {output_file}")

    finally:
        await browser.close()
        print("🔒 Navigateur fermé")


if __name__ == "__main__":
    asyncio.run(main())
