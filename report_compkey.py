#!/usr/bin/env python3
"""Rapport rapide sur les posts avec URN compkey.

Usage:
    uv run python report_compkey.py
    uv run python report_compkey.py output/feed.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("output/feed.json")


def _load_posts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Le fichier JSON doit contenir une liste de posts.")
    return [x for x in data if isinstance(x, dict)]


def main() -> int:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    try:
        posts = _load_posts(input_path)
    except Exception as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1

    compkey_posts = [p for p in posts if str(p.get("urn", "")).startswith("urn:li:compkey:")]

    print(f"Source: {input_path}")
    print(f"Total posts: {len(posts)}")
    print(f"Posts compkey: {len(compkey_posts)}")
    print("-" * 80)

    if not compkey_posts:
        print("Aucun post compkey trouvé.")
        return 0

    for i, post in enumerate(compkey_posts, 1):
        author = post.get("author_name") or "?"
        urn = post.get("urn") or "?"
        url = post.get("linkedin_url")
        status = post.get("ui_permalink_fallback_status")
        error = post.get("ui_permalink_fallback_error")
        candidates = post.get("permalink_candidates") or []
        if not isinstance(candidates, list):
            candidates = []

        print(f"[{i}] {author}")
        print(f"  urn        : {urn}")
        print(f"  url        : {url or '-'}")
        print(f"  status     : {status or '-'}")
        print(f"  error      : {error or '-'}")
        print(f"  candidates : {len(candidates)}")
        if candidates:
            print(f"  top        : {candidates[0]}")
        print("-" * 80)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
