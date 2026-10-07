"""Outils de session de fil (spec lots de scraping, R55-R61).

Lancer : uv run python test_feed_sessions.py
"""
import asyncio
import json
import logging
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_scraper import CheckpointError, RateLimitError
from linkedin_scraper.core.rate_limit_guard import CooldownActiveError

from linkedin_mcp import server


class FakePage:
    def __init__(self, url="about:blank", window_name=""):
        self.url = url
        self.closed = False
        self.gotos = []
        self.fronts = 0
        self.window_name = window_name
        self.handlers = {}
        self.main_frame = object()

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def is_closed(self):
        return self.closed

    async def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = url

    async def close(self):
        self.closed = True

    async def bring_to_front(self):
        self.fronts += 1

    async def evaluate(self, script):
        if "window.name =" in script:
            self.window_name = server._FEED_SESSION_TAB_MARKER
            return None
        return self.window_name


class FakeContext:
    def __init__(self):
        self.pages = []

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowserManager:
    def __init__(self):
        self.page = FakePage()
        self.context = FakeContext()

    async def close(self):
        pass


class FakePost:
    def __init__(self, n):
        self.n = n

    def to_public_dict(self):
        return {"urn": f"urn:li:activity:{7000000000000000000 + self.n}", "text": f"Post {self.n}"}


class FakeFeedScraper:
    """Rend 3 posts par appel, sans jamais en rendre deux fois le même."""

    load_ok = True
    open_error = None
    after_open = None

    def __init__(self, page):
        self.page = page
        self.next_n = 1
        self.calls = 0

    async def open_feed(self):
        if FakeFeedScraper.open_error:
            raise FakeFeedScraper.open_error
        if FakeFeedScraper.load_ok:
            self.page.url = "https://www.linkedin.com/feed/"
        if FakeFeedScraper.after_open:
            await FakeFeedScraper.after_open(self.page)
        return FakeFeedScraper.load_ok

    async def scrape_next(self, limit=10):
        self.calls += 1
        posts = [FakePost(self.next_n + i) for i in range(limit)]
        self.next_n += limit
        return posts, False


def _install():
    manager = FakeBrowserManager()
    server._browser_manager = manager
    server._browser_initialized = True
    server._feed_sessions.pop_all()
    FakeFeedScraper.load_ok = True
    FakeFeedScraper.open_error = None
    FakeFeedScraper.after_open = None
    server._FEED_SESSION_SETTLE_QUIET_S = 0.05
    server._FEED_SESSION_SETTLE_TIMEOUT_S = 0.5
    server._FEED_SESSION_SETTLE_POLL_S = 0.01

    async def fake_get_browser():
        return manager

    return manager, patch.object(server, "_get_browser", fake_get_browser), patch.object(
        server, "FeedScraper", FakeFeedScraper
    )


def _uninstall():
    server._feed_sessions.pop_all()
    server._browser_manager = None
    server._browser_initialized = False


def _run(coro):
    return asyncio.run(coro)


def test_begin_puis_deux_next_rendent_des_posts_distincts_sans_recharger():
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            lot1 = json.loads(_run(server.next_feed_posts(session_id=sid, count=3)))
            lot2 = json.loads(_run(server.next_feed_posts(session_id=sid, count=3)))
        assert len(manager.context.pages) == 1, "un onglet dédié par session"
        onglet = manager.context.pages[0]
        assert onglet.gotos == [], "l'onglet de session n'est jamais garé ni rechargé"
        assert manager.page.gotos == [], "R55 — l'onglet partagé n'est pas touché"
        assert manager.page.fronts >= 3, "premier plan rendu à l'onglet partagé après chaque outil"
        urns = [p["urn"] for p in lot1["posts"] + lot2["posts"]]
        assert len(urns) == 6 and len(set(urns)) == 6
        assert lot1["exhausted"] is False
    finally:
        _uninstall()


def test_le_verrou_est_libere_entre_deux_next():
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]

            async def scenario():
                lock = server._get_browser_lock()
                assert not lock.locked()
                await server.next_feed_posts(session_id=sid, count=1)
                return lock.locked()

            assert _run(scenario()) is False
    finally:
        _uninstall()


def test_un_second_begin_ferme_la_session_precedente():
    manager, g, f = _install()
    try:
        with g, f:
            sid1 = json.loads(_run(server.begin_feed_session()))["session_id"]
            _vieillir(sid1)
            sid2 = json.loads(_run(server.begin_feed_session()))["session_id"]
            assert sid1 != sid2
            assert manager.context.pages[0].closed is True
            assert manager.context.pages[1].closed is False
            try:
                _run(server.next_feed_posts(session_id=sid1, count=1))
            except RuntimeError as exc:
                assert "session de fil inconnue" in str(exc), str(exc)
            else:
                raise AssertionError("l'ancienne session devait être inconnue")
    finally:
        _uninstall()


def test_end_ferme_l_onglet_et_est_idempotent():
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            assert json.loads(_run(server.end_feed_session(session_id=sid))) == {"closed": True}
            assert manager.context.pages[0].closed is True
            assert json.loads(_run(server.end_feed_session(session_id=sid))) == {"closed": False}
    finally:
        _uninstall()


def test_next_sur_une_session_inconnue():
    manager, g, f = _install()
    try:
        with g, f:
            try:
                _run(server.next_feed_posts(session_id="nope", count=1))
            except RuntimeError as exc:
                assert str(exc).startswith("session de fil inconnue"), str(exc)
            else:
                raise AssertionError("erreur attendue")
    finally:
        _uninstall()


def test_begin_referme_l_onglet_si_le_fil_ne_charge_pas():
    manager, g, f = _install()
    try:
        FakeFeedScraper.load_ok = False
        with g, f:
            try:
                _run(server.begin_feed_session())
            except RuntimeError as exc:
                assert "session de fil" in str(exc), str(exc)
            else:
                raise AssertionError("erreur attendue")
        assert manager.context.pages[0].closed is True
        assert len(server._feed_sessions) == 0
    finally:
        _uninstall()


def test_une_limitation_linkedin_est_reconnaissable():
    manager, g, f = _install()
    try:
        for err in (RateLimitError("x"), CheckpointError("y"), CooldownActiveError("z", 60)):
            FakeFeedScraper.open_error = err
            with g, f:
                try:
                    _run(server.begin_feed_session())
                except RuntimeError as exc:
                    assert str(exc).startswith("limitation LinkedIn"), (type(err), str(exc))
                else:
                    raise AssertionError("erreur attendue")
            assert manager.context.pages[-1].closed is True

        FakeFeedScraper.open_error = None
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            sess = server._feed_sessions.get(sid)

            async def limite(limit=10):
                raise RateLimitError("checkpoint")

            sess.scraper.scrape_next = limite
            try:
                _run(server.next_feed_posts(session_id=sid, count=1))
            except RuntimeError as exc:
                assert str(exc).startswith("limitation LinkedIn"), str(exc)
            else:
                raise AssertionError("erreur attendue")
    finally:
        _uninstall()


def test_fermer_le_navigateur_purge_les_sessions():
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            onglet = manager.context.pages[0]
            _run(server._close_browser_singleton())
        assert onglet.closed is True
        assert len(server._feed_sessions) == 0
        manager2, g2, f2 = _install()
        with g2, f2:
            try:
                _run(server.next_feed_posts(session_id=sid, count=1))
            except RuntimeError as exc:
                assert "session de fil inconnue" in str(exc), str(exc)
            else:
                raise AssertionError("erreur attendue")
    finally:
        _uninstall()


def test_un_onglet_perdu_en_cours_de_lecture_rend_la_session_inconnue():
    # close_scrape_browser / create_scrape_session ne prennent pas le verrou :
    # l'onglet peut disparaître pendant un next. Le worker doit recevoir
    # « session de fil inconnue » pour rouvrir une session (§6), pas épuiser
    # ses tentatives sur une page morte.
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            sess = server._feed_sessions.get(sid)
            sess.page.closed = True

            async def morte(limit=10):
                raise RuntimeError("Target page, context or browser has been closed")

            sess.scraper.scrape_next = morte
            try:
                _run(server.next_feed_posts(session_id=sid, count=1))
            except RuntimeError as exc:
                assert "session de fil inconnue" in str(exc), str(exc)
            else:
                raise AssertionError("erreur attendue")
        assert len(server._feed_sessions) == 0
    finally:
        _uninstall()


def test_begin_ferme_un_onglet_de_session_orphelin():
    # En contexte persistant CDP, l'onglet survit à un redémarrage du pod
    # linkedin-mcp (fuite d'onglets, af51454). Marqué par window.name, il est
    # retrouvé et fermé au begin suivant.
    manager, g, f = _install()
    orphelin = FakePage(url="https://www.linkedin.com/feed/", window_name=server._FEED_SESSION_TAB_MARKER)
    etranger = FakePage(url="https://example.org/")
    manager.context.pages.extend([orphelin, etranger])
    try:
        with g, f:
            _run(server.begin_feed_session())
        assert orphelin.closed is True
        assert etranger.closed is False
        assert manager.page.closed is False
    finally:
        _uninstall()


def test_next_borne_le_nombre_de_posts_demande():
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            lot = json.loads(_run(server.next_feed_posts(session_id=sid, count=500)))
        assert len(lot["posts"]) == server._FEED_SESSION_MAX_COUNT
    finally:
        _uninstall()


def test_le_nettoyeur_ferme_une_session_expiree():
    manager, g, f = _install()
    try:
        with g, f:
            json.loads(_run(server.begin_feed_session()))
            onglet = manager.context.pages[0]
            for sess in list(server._feed_sessions._sessions.values()):
                sess.last_used -= server._FEED_SESSION_IDLE_TTL_S + 1
            fermees = _run(server._reap_feed_sessions_once())
        assert fermees == 1
        assert onglet.closed is True
        assert len(server._feed_sessions) == 0
    finally:
        _uninstall()


class FrozenPage(FakePage):
    """Onglet d'arrière-plan gelé : evaluate ne répond qu'après bring_to_front."""

    def __init__(self, url, window_name=""):
        super().__init__(url=url, window_name=window_name)
        self.evaluations = 0

    async def evaluate(self, script):
        self.evaluations += 1
        if self.fronts == 0:
            await asyncio.Event().wait()
        return self.window_name


class ForeignPage(FakePage):
    async def evaluate(self, script):
        self.evaluations = getattr(self, "evaluations", 0) + 1
        return self.window_name


def test_begin_annule_ferme_l_onglet_dedie():
    manager, g, f = _install()

    async def hang(self):
        await asyncio.Event().wait()

    try:
        with g, patch.object(server, "FeedScraper", FakeFeedScraper), patch.object(
            FakeFeedScraper, "open_feed", hang
        ):

            async def scenario():
                task = asyncio.ensure_future(server.begin_feed_session())
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            _run(scenario())
        assert len(manager.context.pages) == 1
        assert manager.context.pages[0].closed is True
        assert len(server._feed_sessions) == 0
    finally:
        _uninstall()


def test_begin_ne_fuit_pas_si_le_marquage_se_fige():
    manager, g, f = _install()
    try:
        with g, f, patch.object(server, "_FEED_SESSION_MARK_TIMEOUT_S", 0.05):

            async def hang_mark(self, script):
                if "window.name =" in script:
                    await asyncio.Event().wait()
                return self.window_name

            with patch.object(FakePage, "evaluate", hang_mark):
                sid = json.loads(_run(server.begin_feed_session()))["session_id"]
        assert manager.context.pages[0].closed is False
        assert server._feed_sessions.get(sid) is not None
    finally:
        _uninstall()


def test_next_ferme_un_onglet_plante_non_ferme():
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            sess = server._feed_sessions.get(sid)

            async def crash(limit=10):
                raise RuntimeError("Page.evaluate: Page crashed")

            sess.scraper.scrape_next = crash
            assert sess.page.closed is False
            try:
                _run(server.next_feed_posts(session_id=sid, count=1))
            except RuntimeError as exc:
                assert "session de fil inconnue" in str(exc), str(exc)
            else:
                raise AssertionError("erreur attendue")
        assert sess.page.closed is True
        assert len(server._feed_sessions) == 0
    finally:
        _uninstall()


def test_le_balayage_retrouve_un_orphelin_gele_sans_toucher_aux_onglets_etrangers():
    manager, g, f = _install()
    orphelin = FrozenPage("https://www.linkedin.com/feed/", server._FEED_SESSION_TAB_MARKER)
    linkedin_normal = FrozenPage("https://www.linkedin.com/in/someone/")
    etranger = ForeignPage(url="https://example.org/")
    manager.context.pages.extend([orphelin, linkedin_normal, etranger])
    try:
        with g, f, patch.object(server, "_FEED_SESSION_PROBE_TIMEOUT_S", 0.2):
            _run(server.begin_feed_session())
        assert orphelin.closed is True
        assert linkedin_normal.closed is False
        assert etranger.closed is False
        assert etranger.fronts == 0 and getattr(etranger, "evaluations", 0) == 0
        assert manager.page.fronts >= 1, "premier plan rendu à l'onglet partagé"
    finally:
        _uninstall()


def test_le_plafond_de_posts_ferme_la_session_et_rend_exhausted():
    manager, g, f = _install()
    try:
        with g, f, patch.object(server, "_FEED_SESSION_MAX_POSTS", 5):
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            lot1 = json.loads(_run(server.next_feed_posts(session_id=sid, count=3)))
            assert lot1["exhausted"] is False and len(lot1["posts"]) == 3
            assert len(server._feed_sessions) == 1, "sous le plafond la session reste"
            lot2 = json.loads(_run(server.next_feed_posts(session_id=sid, count=3)))
            assert len(lot2["posts"]) == 3 and lot2["exhausted"] is True
            assert len(server._feed_sessions) == 0
            assert manager.context.pages[0].closed is True
            try:
                _run(server.next_feed_posts(session_id=sid, count=1))
            except RuntimeError as exc:
                assert "session de fil inconnue" in str(exc), str(exc)
            else:
                raise AssertionError("erreur attendue après le plafond")
    finally:
        _uninstall()


def test_begin_marque_son_propre_onglet():
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            _run(server.next_feed_posts(session_id=sid, count=1))
        assert manager.context.pages[-1].window_name == server._FEED_SESSION_TAB_MARKER
    finally:
        _uninstall()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage()))


def _capture():
    cap = _Capture()
    server.logger.addHandler(cap)
    old = server.logger.level
    server.logger.setLevel(logging.DEBUG)
    return cap, lambda: (server.logger.removeHandler(cap), server.logger.setLevel(old))


def test_la_navigation_de_l_onglet_de_session_est_journalisee():
    manager, g, f = _install()
    cap, undo = _capture()
    try:
        with g, f:
            _run(server.begin_feed_session())
        page = manager.context.pages[0]
        assert len(page.handlers.get("framenavigated", [])) == 1
        handler = page.handlers["framenavigated"][0]
        cap.records.clear()

        class Frame:
            url = "https://www.linkedin.com/checkpoint/challenge/abc"

        frame = Frame()
        page.main_frame = frame
        handler(frame)
        assert any(
            lvl == logging.INFO and frame.url in msg for lvl, msg in cap.records
        ), cap.records
        cap.records.clear()
        handler(Frame())  # autre frame que le principal
        assert cap.records == []
        handler(object())  # objet cassé : ne lève jamais
        handler(None)
    finally:
        undo()
        _uninstall()


def test_next_perdu_donne_l_url_dans_le_message():
    manager, g, f = _install()
    cap, undo = _capture()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            sess = server._feed_sessions.get(sid)
            sess.page.url = "https://www.linkedin.com/checkpoint/x"

            async def detruit(limit=10):
                raise RuntimeError("Page.evaluate: Execution context was destroyed, most likely because of a navigation")

            sess.scraper.scrape_next = detruit
            try:
                _run(server.next_feed_posts(session_id=sid, count=1))
            except RuntimeError as exc:
                assert "session de fil inconnue" in str(exc), str(exc)
                assert "url=https://www.linkedin.com/checkpoint/x" in str(exc), str(exc)
            else:
                raise AssertionError("erreur attendue")
        assert any(
            lvl == logging.WARNING and "url=https://www.linkedin.com/checkpoint/x" in msg
            for lvl, msg in cap.records
        ), cap.records
    finally:
        undo()
        _uninstall()


def test_next_reussi_journalise_url_posts_et_exhausted():
    manager, g, f = _install()
    cap, undo = _capture()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            manager.context.pages[0].url = "https://www.linkedin.com/feed/"
            _run(server.next_feed_posts(session_id=sid, count=2))
        assert any(
            lvl == logging.INFO
            and "url=https://www.linkedin.com/feed/" in msg
            and "2 posts" in msg
            and "exhausted=False" in msg
            and "total=2" in msg
            for lvl, msg in cap.records
        ), cap.records
    finally:
        undo()
        _uninstall()


def _naviguer(page, url):
    """Simule une navigation du cadre principal, comme Playwright."""

    class Frame:
        pass

    frame = Frame()
    frame.url = url
    page.main_frame = frame
    page.url = url
    for handler in page.handlers.get("framenavigated", []):
        handler(frame)


def test_begin_attend_la_fin_des_redirections_de_l_onglet():
    # Essai réel du 2026-10-05 : /feed -> /uas/login -> /login -> /feed avant
    # que la page soit utilisable. Un begin qui rend la main pendant ce rebond
    # laisse le premier next tomber sur « Execution context was destroyed ».
    manager, g, f = _install()
    try:
        etapes = []

        async def rebond(page):
            _naviguer(page, "https://www.linkedin.com/login/?session_redirect=x")

            async def retour():
                await asyncio.sleep(0.15)
                _naviguer(page, "https://www.linkedin.com/feed/")
                etapes.append("retour sur le fil")

            asyncio.get_running_loop().create_task(retour())

        FakeFeedScraper.after_open = rebond
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
        assert etapes == ["retour sur le fil"], "begin a rendu la main pendant le rebond"
        assert server._feed_sessions.get(sid).page.url == "https://www.linkedin.com/feed/"
    finally:
        _uninstall()


def test_begin_echoue_si_l_onglet_ne_revient_pas_sur_le_fil():
    manager, g, f = _install()
    try:

        async def bloque(page):
            _naviguer(page, "https://www.linkedin.com/checkpoint/challenge/abc")

        FakeFeedScraper.after_open = bloque
        with g, f:
            try:
                _run(server.begin_feed_session())
            except RuntimeError as exc:
                assert "/checkpoint/challenge/abc" in str(exc), str(exc)
            else:
                raise AssertionError("erreur attendue")
        assert manager.context.pages[0].closed is True
        assert len(server._feed_sessions) == 0
    finally:
        _uninstall()


def _vieillir(session_id):
    """Simule une session inutilisée depuis plus que la fenêtre d'activité."""
    sess = server._feed_sessions.get(session_id)
    sess.last_used -= server._FEED_SESSION_BUSY_WINDOW_S + 1


def test_begin_refuse_si_une_autre_session_est_active():
    # 2026-10-06 : un run cron et un run manuel simultanés se sont volé leurs
    # sessions (4 rechargements du fil, un run arrêté en sans_session).
    manager, g, f = _install()
    try:
        with g, f:
            sid1 = json.loads(_run(server.begin_feed_session()))["session_id"]
            try:
                _run(server.begin_feed_session())
            except RuntimeError as exc:
                assert "session de fil occupée" in str(exc), str(exc)
            else:
                raise AssertionError("le second begin devait être refusé")
            lot = json.loads(_run(server.next_feed_posts(session_id=sid1, count=2)))
        assert len(lot["posts"]) == 2, "la session active reste utilisable"
        assert manager.context.pages[0].closed is False
        assert len(manager.context.pages) == 1, "aucun onglet ouvert pour le refus"
    finally:
        _uninstall()


def test_next_compte_comme_activite_a_la_fin_de_la_lecture():
    # La fenêtre se mesure depuis la FIN du dernier appel : une lecture dure
    # ~50 s, et le worker trie ensuite le lot avant de redemander.
    manager, g, f = _install()
    try:
        with g, f:
            sid = json.loads(_run(server.begin_feed_session()))["session_id"]
            _vieillir(sid)
            _run(server.next_feed_posts(session_id=sid, count=1))
            assert server._feed_sessions.active_since(server._FEED_SESSION_BUSY_WINDOW_S) is not None
    finally:
        _uninstall()


if __name__ == "__main__":
    for nom, fn in list(globals().items()):
        if nom.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {nom} OK")
