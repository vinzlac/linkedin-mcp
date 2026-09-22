#!/usr/bin/env python3
"""Tests (sans navigateur) du garage de l'onglet partagé entre deux outils.

Le Chromium de l'hôte (chromium-cdp.service, MemoryMax=4G) garde l'onglet sur
la dernière page LinkedIn visitée : feed déroulé, fils de commentaires ouverts,
pages détail — un renderer de plusieurs Go qui ne redescend jamais entre deux
appels. Le 2026-09-21, un scrape feed a poussé le cgroup à 3,26 G, au-dessus
de son MemoryHigh : Chromium a été étranglé deux heures, deux runs de
linkedin-sync ont échoué sur `connect_over_cdp` (timeout 180 s) et le nœud a
craché ~1 000 fautes de page majeures par seconde.

Garer l'onglet sur about:blank après chaque outil rend le heap du renderer ;
chaque scraper re-navigue de toute façon vers sa page cible.

Usage:
    uv run python test_browser_parking.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp import server
from linkedin_mcp.server import serialize_browser_access


class FakePage:
    def __init__(self, url="https://www.linkedin.com/feed/", closed=False, fail=False):
        self.url = url
        self._closed = closed
        self._fail = fail
        self.gotos = []

    def is_closed(self):
        return self._closed

    async def goto(self, url, **kwargs):
        if self._fail:
            raise RuntimeError("Target page, context or browser has been closed")
        self.gotos.append((url, kwargs))
        self.url = url


class FakeBrowserManager:
    def __init__(self, page):
        self.page = page


def _install(page):
    server._browser_manager = FakeBrowserManager(page)
    server._browser_initialized = True
    return page


def _uninstall():
    server._browser_manager = None
    server._browser_initialized = False


def test_the_page_is_parked_after_a_tool_run():
    page = _install(FakePage())

    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def outil():
        return "ok"

    try:
        assert asyncio.run(outil()) == "ok"
        assert page.gotos and page.gotos[0][0] == "about:blank", page.gotos
        assert page.url == "about:blank"
    finally:
        _uninstall()


def test_the_page_is_parked_even_when_the_tool_raises():
    page = _install(FakePage())

    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def echoue():
        raise ValueError("boum")

    try:
        try:
            asyncio.run(echoue())
        except ValueError:
            pass
        assert page.url == "about:blank"
    finally:
        _uninstall()


def test_an_already_parked_page_is_left_alone():
    page = _install(FakePage(url="about:blank"))

    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def outil():
        return "ok"

    try:
        asyncio.run(outil())
        assert page.gotos == [], page.gotos
    finally:
        _uninstall()


def test_a_parking_failure_never_hides_the_tool_result():
    """Une page crashée fera échouer le goto : le garage est best effort, le
    résultat de l'outil et la libération du verrou passent avant."""
    _install(FakePage(fail=True))

    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def outil():
        return "résultat"

    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def ensuite():
        return "passé"

    async def main():
        premier = await outil()
        second = await ensuite()
        return premier, second

    try:
        assert asyncio.run(main()) == ("résultat", "passé")
    finally:
        _uninstall()


def test_nothing_happens_without_a_browser():
    _uninstall()

    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def outil():
        return "ok"

    assert asyncio.run(outil()) == "ok"


if __name__ == "__main__":
    test_the_page_is_parked_after_a_tool_run()
    test_the_page_is_parked_even_when_the_tool_raises()
    test_an_already_parked_page_is_left_alone()
    test_a_parking_failure_never_hides_the_tool_result()
    test_nothing_happens_without_a_browser()
    print("✅ test_browser_parking OK")
