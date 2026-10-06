"""Essai RÉEL d'une session de fil contre LinkedIn (navigateur et session
locaux). Non lancé en CI. Usage : uv run python test_feed_session_live.py [N]"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp import server


async def main(n: int) -> None:
    sid = json.loads(await server.begin_feed_session())["session_id"]
    vus = set()
    try:
        for lot in range(1, 4):
            res = json.loads(await server.next_feed_posts(session_id=sid, count=n))
            urns = [p.get("urn") for p in res["posts"]]
            doublons = vus.intersection(urns)
            vus.update(urns)
            sess = server._feed_sessions.get(sid)
            cartes = await sess.page.evaluate(
                "() => document.querySelectorAll('[data-urn],[componentkey]').length"
            )
            print(
                f"lot {lot} : {len(urns)} post(s), doublons={len(doublons)}, "
                f"épuisé={res['exhausted']}, cartes dans le DOM={cartes}"
            )
            assert not doublons, doublons
            # L'onglet partagé doit rester vivant aux yeux de la sonde (af51454).
            assert await server._browser_singleton_is_alive(), "sonde de l'onglet partagé en échec"
    finally:
        print(await server.end_feed_session(session_id=sid))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
