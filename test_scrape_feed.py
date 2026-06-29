#!/usr/bin/env python3
"""Test script: scrape a single LinkedIn post via Playwright session.

Usage:
    uv run python test_scrape_feed.py "https://www.linkedin.com/feed/update/urn:li:activity:123/"
    uv run python test_scrape_feed.py "<post_url>" output/post.json
    uv run python test_scrape_feed.py "<post_url>" --dir output
"""
import asyncio
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.config.settings import settings
from linkedin_scraper import BrowserManager, FeedScraper

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_FEED_JSON = REPO_ROOT / "output" / "feed-single.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape un post LinkedIn (URL) via session Playwright."
    )
    parser.add_argument(
        "post_url",
        help="URL du post LinkedIn à scraper (feed/update/...)",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Chemin de sortie JSON, ou '-' pour désactiver l'écriture",
    )
    parser.add_argument(
        "--dir",
        dest="output_dir",
        default=None,
        help=(
            "Dossier parent de sortie. Si fourni, crée automatiquement "
            "<dir>/<YYYYMMDD-HHMMSS>/feed.json"
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    post_url = args.post_url
    out_arg = args.output
    output_dir = args.output_dir

    output_file: Path | None

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = Path(output_dir) / ts / "feed-single.json"
    elif out_arg == "-":
        output_file = None
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
    browser = BrowserManager(headless=settings.LINKEDIN_HEADLESS)
    await browser.start()
    await browser.load_session(session_path)
    print("✅ Session chargée")

    try:
        print(f"\n📰 Scraping du post LinkedIn : {post_url}")
        scraper = FeedScraper(browser.page)
        posts = await scraper.scrape_post_by_url(post_url)

        if not posts:
            print("⚠️  Aucun post trouvé pour cette URL.")
            return

        print("✅ Post récupéré\n")

        data = [p.to_public_dict() for p in posts]

        post = data[0]
        author = post.get("author_name") or "?"
        text_preview = (post.get("text") or "")[:120].replace("\n", " ")
        date = post.get("posted_date") or "?"
        reactions = post.get("reactions_count")
        comments = post.get("comments_count")
        print(f"{author} · {date}")
        print(f"    {text_preview}{'…' if len(post.get('text') or '') > 120 else ''}")
        print(f"    👍 {reactions}  💬 {comments}")
        print(f"    🔗 {post.get('linkedin_url')}")
        print(f"    💬 comments_with_url: {len(post.get('comments') or [])}")
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
