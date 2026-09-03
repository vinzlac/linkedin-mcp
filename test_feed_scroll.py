#!/usr/bin/env python3
"""Tests du scroll de feed (dette relevée le 2026-09-03).

Le feed LinkedIn scrolle désormais dans `<main>` et non plus dans la fenêtre
(`document.scrollHeight == clientHeight`), ce qui rendait no-op tous les
`window.scrollBy(...)` de like_ui / repost_ui — et donc le fallback « carte
feed » incapable d'amener une carte hors écran dans le viewport.

Usage:
    uv run python test_feed_scroll.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.linkedin.feed_scroll import scroll_feed

INNER_SCROLL_HTML = """
<html><body style="margin:0">
  <main style="height:300px;overflow:auto">
    <div style="height:4000px">contenu long</div>
  </main>
</body></html>
"""

WINDOW_SCROLL_HTML = """
<html><body style="margin:0">
  <div style="height:4000px">contenu long</div>
</body></html>
"""

NOTHING_SCROLLABLE_HTML = """<html><body><div>court</div></body></html>"""


async def _run(html: str, dy: int = 500):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 800, "height": 400})
            await page.set_content(html)
            strategy = await scroll_feed(page, dy)
            state = await page.evaluate(
                "() => ({"
                " windowY: window.scrollY,"
                " mainTop: document.querySelector('main') ? document.querySelector('main').scrollTop : null"
                "})"
            )
            return strategy, state
        finally:
            await browser.close()


def test_scrolls_the_inner_container_when_the_window_does_not_scroll():
    strategy, state = asyncio.run(_run(INNER_SCROLL_HTML))
    assert strategy == "main", strategy
    assert state["mainTop"] == 500, state
    assert state["windowY"] == 0, state


def test_still_scrolls_the_window_when_the_document_scrolls():
    strategy, state = asyncio.run(_run(WINDOW_SCROLL_HTML))
    assert strategy == "window", strategy
    assert state["windowY"] > 0, state


def test_reports_when_nothing_is_scrollable():
    strategy, _ = asyncio.run(_run(NOTHING_SCROLLABLE_HTML))
    assert strategy == "none", strategy


if __name__ == "__main__":
    test_scrolls_the_inner_container_when_the_window_does_not_scroll()
    test_still_scrolls_the_window_when_the_document_scrolls()
    test_reports_when_nothing_is_scrollable()
    print("✅ test_feed_scroll OK")
