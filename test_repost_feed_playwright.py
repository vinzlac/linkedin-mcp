#!/usr/bin/env python3
"""Repost first repostable third-party post from feed (Playwright UI)."""
import asyncio
import sys

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--from-feed", "--execute", *sys.argv[1:]]
    from test_repost_ui import main

    asyncio.run(main())
