#!/usr/bin/env python3
"""Test repost via Playwright UI (repost_post_scrape / fallback API 403).

Usage:
    uv run python test_repost_ui.py POST_URL
    uv run python test_repost_ui.py POST_URL --execute
    uv run python test_repost_ui.py --from-feed --execute
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.config.settings import settings
from linkedin_mcp.linkedin.repost_ui import RepostUI, RepostUIError, normalize_post_url
from linkedin_scraper import BrowserManager, FeedScraper
from linkedin_scraper.scrapers.feed import FEED_URL


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test repost Playwright UI")
    parser.add_argument("post_url", nargs="?", help="URL ou URN activity")
    parser.add_argument(
        "--from-feed",
        action="store_true",
        help="Prend le premier post tiers repostable du feed",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Publie vraiment le repost (sinon dry-run URL seulement)",
    )
    parser.add_argument("--commentary", default="", help="Commentaire optionnel")
    args = parser.parse_args()

    if not args.post_url and not args.from_feed:
        parser.error("post_url ou --from-feed requis")

    session_path = settings.LINKEDIN_SESSION_PATH
    if not os.path.exists(session_path):
        print(f"❌ Session Playwright introuvable : {session_path}")
        sys.exit(1)

    browser = BrowserManager(headless=True)
    await browser.start()
    await browser.load_session(session_path)

    try:
        post_url = args.post_url
        if args.from_feed:
            scraper = FeedScraper(browser.page)
            await scraper.navigate_and_wait(FEED_URL)
            posts = await scraper.scrape(limit=10)
            for post in posts:
                d = post.to_public_dict()
                author = (d.get("author_name") or "").lower()
                url = d.get("linkedin_url") or ""
                if not url or "activity:" not in url:
                    continue
                if "vincent lacoste" in author or d.get("actor_name"):
                    continue
                post_url = url
                print(f"📌 Feed : {d.get('author_name')} -> {url}")
                break
            else:
                print("❌ Aucun post repostable dans le feed")
                sys.exit(1)

        normalized = normalize_post_url(post_url)
        print(f"🔗 URL normalisée : {normalized}")

        if not args.execute:
            print("Dry-run — ajoute --execute pour publier")
            return

        ui = RepostUI(browser.page)
        msg = await ui.repost(post_url, commentary=args.commentary)
        print(f"✅ {msg}")
    except RepostUIError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
