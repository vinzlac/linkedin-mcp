#!/usr/bin/env python3
"""Test bout-en-bout : scrape les N premiers posts du feed, puis like + repost le 1er.

Vérifie le scénario complet visé par la correction du hang like/repost :
scraper un post individuel (comme le ferait scrape_post) puis enchaîner
like_post et repost_post sur la MÊME page Playwright, sans navigation
supplémentaire qui bloquait auparavant.

Par défaut le script est en dry-run (aucune action réelle sur LinkedIn) :
il scrape et affiche uniquement ce qui SERAIT liké/reposté. Ajoute --execute
pour vraiment liker et reposter le premier post du feed.

Usage:
    uv run python test_scrape_like_repost_feed.py
    uv run python test_scrape_like_repost_feed.py 5
    uv run python test_scrape_like_repost_feed.py 5 --execute
    uv run python test_scrape_like_repost_feed.py --execute --commentary "Top !"
    uv run python test_scrape_like_repost_feed.py --execute --skip-repost
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.config.settings import settings
from linkedin_mcp.linkedin.like_ui import LikeUI, LikeUIError, AlreadyLikedError
from linkedin_mcp.linkedin.repost import activity_id_from_post_ref
from linkedin_mcp.linkedin.repost_ui import RepostUI, RepostUIError
from linkedin_scraper import BrowserManager, FeedScraper


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape N posts du feed puis like + repost le 1er (dry-run par défaut)."
    )
    parser.add_argument(
        "count",
        nargs="?",
        type=int,
        default=1,
        help="Nombre de posts à scraper depuis le feed (défaut 1)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Publie vraiment le like et le repost (sinon dry-run)",
    )
    parser.add_argument(
        "--commentary",
        default="",
        help="Commentaire optionnel pour le repost (vide = repost instantané)",
    )
    parser.add_argument(
        "--skip-like",
        action="store_true",
        help="Ne fait pas le like (utile pour ne tester que le repost)",
    )
    parser.add_argument(
        "--skip-repost",
        action="store_true",
        help="Ne fait pas le repost (utile pour ne tester que le like)",
    )
    return parser.parse_args()


def _pick_target_post(posts: list) -> tuple[dict, int]:
    """Premier post du feed disposant d'une URL exploitable par like/repost.

    Utilise la même extraction que LikeUI/RepostUI (activity_id_from_post_ref),
    qui accepte aussi bien /feed/update/urn:li:activity:... que
    /posts/{slug}-share-{id}-{suffix}/ — un simple test de sous-chaîne
    "activity:" rejette à tort ce second format, pourtant valide.
    """
    for index, post in enumerate(posts):
        data = post.to_public_dict()
        url = data.get("linkedin_url") or ""
        if url and activity_id_from_post_ref(url):
            return data, index
    raise RuntimeError(
        "Aucun post scrapé n'a d'URL exploitable par like_post/repost_post "
        "(ni urn:li:activity:..., ni /posts/{slug}-share-{id}-{suffix}/)."
    )


async def main() -> int:
    args = _parse_args()

    session_path = settings.LINKEDIN_SESSION_PATH
    if not session_path or not os.path.exists(session_path):
        print(f"❌ Session Playwright introuvable : {session_path}")
        print("   Génère-la avec : cd ../linkedin_scraper && just session")
        return 1
    print(f"✅ Session trouvée : {session_path}")

    print(f"\n🌐 Démarrage du navigateur (headless={settings.LINKEDIN_HEADLESS})...")
    browser = BrowserManager(headless=settings.LINKEDIN_HEADLESS)
    await browser.start()
    await browser.load_session(session_path)
    print("✅ Session chargée")

    exit_code = 0
    try:
        print(f"\n▶ Scraping des {args.count} premier(s) post(s) du feed...")
        scraper = FeedScraper(browser.page)
        posts = await scraper.scrape(limit=args.count)

        if not posts:
            print("❌ Aucun post scrapé — feed vide ou non chargé.")
            return 1

        print(f"✅ {len(posts)} post(s) scrapé(s) :")
        for i, post in enumerate(posts):
            d = post.to_public_dict()
            author = d.get("author_name") or "?"
            preview = (d.get("text") or "")[:80].replace("\n", " ")
            print(f"   [{i}] {author} — {preview}…")
            print(f"       👍 {d.get('reactions_count')}  💬 {d.get('comments_count')}"
                  f"  🔗 {d.get('linkedin_url')}")

        target, index = _pick_target_post(posts)
        post_url = target["linkedin_url"]
        print(f"\n🎯 Cible retenue : post [{index}] — {target.get('author_name')}")
        print(f"   {post_url}")

        if not args.execute:
            print("\nDry-run — aucune action publiée. Ajoute --execute pour liker/reposter.")
            print(f"   -> aurait liké : {not args.skip_like}")
            print(f"   -> aurait reposté (commentaire={args.commentary!r}) : {not args.skip_repost}")
            return 0

        if not args.skip_like:
            print("\n▶ Like en cours...")
            try:
                msg = await LikeUI(browser.page).like(post_url)
                print(f"✅ {msg}")
            except AlreadyLikedError as exc:
                print(f"ℹ️  {exc}")
            except LikeUIError as exc:
                print(f"❌ Échec like : {exc}")
                exit_code = 1
        else:
            print("\n⏭️  Like ignoré (--skip-like)")

        if not args.skip_repost:
            print("\n▶ Repost en cours...")
            try:
                msg = await RepostUI(browser.page).repost(
                    post_url, commentary=args.commentary
                )
                print(f"✅ {msg}")
            except RepostUIError as exc:
                print(f"❌ Échec repost : {exc}")
                exit_code = 1
        else:
            print("\n⏭️  Repost ignoré (--skip-repost)")

        return exit_code
    finally:
        await browser.close()
        print("\n🔒 Navigateur fermé")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
