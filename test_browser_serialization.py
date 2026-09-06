#!/usr/bin/env python3
"""Tests de la sérialisation des outils navigateur.

Le serveur ne dispose que d'un seul navigateur, d'un seul contexte et d'un seul
onglet, partagés par tous les outils et tous les appelants (la tâche planifiée
de scraping et le service linkedin-sync, qui interroge le MCP en boucle). Aucun
verrou n'existait : deux appels simultanés naviguaient dans la même page.

Observé en production le 2026-09-04 : un cycle de linkedin-sync est tombé au
milieu d'un scrape, la sonde de vivacité a trouvé la page occupée, en a conclu
que le navigateur était mort et l'a relancé — emportant le scrape avec lui.

Usage:
    uv run python test_browser_serialization.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.server import BrowserBusyError, serialize_browser_access


def test_second_caller_waits_for_the_first():
    ordre = []

    @serialize_browser_access(wait_s=5, timeout_s=10)
    async def lent():
        ordre.append("lent:début")
        await asyncio.sleep(0.3)
        ordre.append("lent:fin")

    @serialize_browser_access(wait_s=5, timeout_s=10)
    async def rapide():
        ordre.append("rapide:début")
        ordre.append("rapide:fin")

    async def main():
        await asyncio.gather(lent(), rapide())

    asyncio.run(main())
    assert ordre == ["lent:début", "lent:fin", "rapide:début", "rapide:fin"], ordre


def test_a_caller_that_waits_too_long_gets_an_explicit_error():
    @serialize_browser_access(wait_s=10, timeout_s=10)
    async def occupant():
        await asyncio.sleep(0.5)

    @serialize_browser_access(wait_s=0.05, timeout_s=10)
    async def presse():
        return "jamais atteint"

    async def main():
        tache = asyncio.create_task(occupant())
        await asyncio.sleep(0.05)
        try:
            await presse()
        except BrowserBusyError as exc:
            assert "occupé" in str(exc).lower(), str(exc)
            return "erreur explicite"
        finally:
            await tache
        return "aucune erreur"

    assert asyncio.run(main()) == "erreur explicite"


def test_the_lock_is_released_when_the_tool_raises():
    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def echoue():
        raise ValueError("boum")

    @serialize_browser_access(wait_s=1, timeout_s=10)
    async def ensuite():
        return "passé"

    async def main():
        try:
            await echoue()
        except ValueError:
            pass
        return await ensuite()

    assert asyncio.run(main()) == "passé"


def test_a_hung_tool_cannot_hold_the_lock_forever():
    """Sans borne sur le détenteur, le verrou remplacerait une collision par un
    interblocage : un scrape parti en vrille bloquerait tout le monde."""

    @serialize_browser_access(wait_s=1, timeout_s=0.2)
    async def bloque():
        await asyncio.sleep(30)

    @serialize_browser_access(wait_s=2, timeout_s=10)
    async def ensuite():
        return "passé"

    async def main():
        try:
            await bloque()
        except TimeoutError:
            pass
        return await ensuite()

    assert asyncio.run(main()) == "passé"


if __name__ == "__main__":
    test_second_caller_waits_for_the_first()
    test_a_caller_that_waits_too_long_gets_an_explicit_error()
    test_the_lock_is_released_when_the_tool_raises()
    test_a_hung_tool_cannot_hold_the_lock_forever()
    print("✅ test_browser_serialization OK")
