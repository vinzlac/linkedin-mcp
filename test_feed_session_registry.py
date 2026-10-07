"""Registre des sessions de fil (spec lots de scraping, R58-R60).

Lancer : uv run python test_feed_session_registry.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.linkedin.feed_sessions import FeedSessionRegistry, UnknownFeedSessionError


class Horloge:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_open_rend_un_identifiant_et_get_le_retrouve():
    reg = FeedSessionRegistry(clock=Horloge())
    sess, remplacee = reg.open("page", "scraper")
    assert remplacee is None
    assert reg.get(sess.session_id) is sess
    assert len(reg) == 1


def test_une_seule_session_a_la_fois():
    reg = FeedSessionRegistry(clock=Horloge())
    premiere, _ = reg.open("p1", "s1")
    seconde, remplacee = reg.open("p2", "s2")
    assert remplacee is premiere
    assert len(reg) == 1
    try:
        reg.get(premiere.session_id)
    except UnknownFeedSessionError as exc:
        assert str(exc).startswith("session de fil inconnue"), str(exc)
    else:
        raise AssertionError("la session remplacée devait être inconnue")
    assert reg.get(seconde.session_id) is seconde


def test_identifiant_inconnu():
    reg = FeedSessionRegistry(clock=Horloge())
    try:
        reg.get("nope")
    except UnknownFeedSessionError as exc:
        assert str(exc) == "session de fil inconnue : nope"
    else:
        raise AssertionError("UnknownFeedSessionError attendue")


def test_expiration_apres_inactivite():
    horloge = Horloge()
    reg = FeedSessionRegistry(idle_ttl_s=600, clock=horloge)
    sess, _ = reg.open("p", "s")
    horloge.t += 599
    assert reg.pop_expired() == []
    horloge.t += 2
    assert reg.pop_expired() == [sess]
    assert len(reg) == 0


def test_get_repousse_l_expiration():
    horloge = Horloge()
    reg = FeedSessionRegistry(idle_ttl_s=600, clock=horloge)
    sess, _ = reg.open("p", "s")
    horloge.t += 500
    reg.get(sess.session_id)
    horloge.t += 500
    assert reg.pop_expired() == []


def test_pop_et_pop_all():
    reg = FeedSessionRegistry(clock=Horloge())
    sess, _ = reg.open("p", "s")
    assert reg.pop("autre") is None
    assert reg.pop(sess.session_id) is sess
    assert reg.pop(sess.session_id) is None
    sess2, _ = reg.open("p", "s")
    assert reg.pop_all() == [sess2]
    assert len(reg) == 0


def test_active_depuis_rend_la_session_utilisee_recemment():
    # R58 bis — deux runs simultanés se volaient leurs sessions (2026-10-06).
    h = Horloge()
    reg = FeedSessionRegistry(clock=h)
    sess, _ = reg.open("page", "scraper")
    h.t += 100
    assert reg.active_since(120) is sess
    h.t += 30
    assert reg.active_since(120) is None


def test_touch_repousse_l_activite():
    h = Horloge()
    reg = FeedSessionRegistry(clock=h)
    sess, _ = reg.open("page", "scraper")
    h.t += 200
    reg.touch(sess)
    assert reg.active_since(120) is sess


if __name__ == "__main__":
    for nom, fn in list(globals().items()):
        if nom.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {nom} OK")
