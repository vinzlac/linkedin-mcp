#!/usr/bin/env python3
"""Spike : repost LinkedIn via POST /rest/posts.

Usage:
    uv run python test_repost_post.py POST_URL_OR_URN
    uv run python test_repost_post.py POST_URL_OR_URN --dry-run
    uv run python test_repost_post.py POST_URL_OR_URN --execute "Mon commentaire"
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.config.settings import settings
from linkedin_mcp.linkedin.auth import LinkedInOAuth, AuthError, UserInfo
from linkedin_mcp.linkedin.post import PostVisibility
from linkedin_mcp.linkedin.repost import RepostManager, RepostError


def _find_existing_token() -> str | None:
    token_dir = settings.TOKEN_STORAGE_PATH
    if not os.path.isdir(token_dir):
        return None
    for filename in os.listdir(token_dir):
        if filename.endswith(".json"):
            return filename[:-5]
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Spike repost LinkedIn REST API")
    parser.add_argument(
        "post_ref",
        help="URL LinkedIn, urn:li:activity:ID, ou ID numérique",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche le payload sans appeler l'API (défaut sans --execute)",
    )
    parser.add_argument(
        "--execute",
        metavar="COMMENTARY",
        nargs="?",
        const="",
        help="Exécute vraiment le repost (commentaire optionnel)",
    )
    parser.add_argument(
        "--visibility",
        choices=["PUBLIC", "CONNECTIONS"],
        default="PUBLIC",
    )
    args = parser.parse_args()

    dry_run = args.dry_run or args.execute is None
    commentary = args.execute if args.execute is not None else ""

    auth_client = LinkedInOAuth()
    user_id = _find_existing_token()
    if not user_id:
        print("❌ Aucun token OAuth. Lance test_create_post.py ou authenticate MCP.")
        sys.exit(1)

    if not auth_client.load_tokens(user_id):
        print("❌ Token corrompu ou illisible.")
        sys.exit(1)

    try:
        user_info = await auth_client.get_user_info()
        print(f"✅ Token chargé : {user_info.name} ({user_info.sub})")
    except AuthError as exc:
        if dry_run:
            auth_client._user_info = UserInfo(
                sub=user_id,
                name=user_id,
                given_name="",
                family_name="",
            )
            print(f"⚠️  userinfo indisponible ({exc}) — dry-run avec sub={user_id}")
        else:
            print(f"❌ Token invalide ou expiré : {exc}")
            print("   Relance authenticate ou test_create_post.py pour renouveler.")
            sys.exit(1)

    manager = RepostManager(auth_client)
    visibility = PostVisibility(args.visibility)

    if dry_run:
        print("\n🔍 Mode dry-run (aucun repost publié)\n")
        preview = await manager.repost(
            args.post_ref,
            commentary=commentary,
            visibility=visibility,
            dry_run=True,
        )
        print(preview)
        print("\nPour publier : ajoute --execute \"commentaire optionnel\"")
        return

    print(f"\n📢 Repost en cours (commentaire={commentary!r})...")
    try:
        repost_id = await manager.repost(
            args.post_ref,
            commentary=commentary,
            visibility=visibility,
        )
        print(f"✅ Repost créé : {repost_id}")
    except RepostError as exc:
        print(f"❌ Échec repost : {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
