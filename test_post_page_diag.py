"""Unit tests du diagnostic de page post (BUG #2 du 2026-09-03).

Le message « Le post n'existe peut-être plus ou la session a expiré » était
émis pour tout bouton introuvable, y compris quand le post existait et que la
session était valide — il envoyait sur de fausses pistes. Ces tests couvrent la
distinction entre session expirée, post introuvable et barre d'action absente.
"""
import asyncio

from linkedin_mcp.linkedin.post_page import (
    POST_PAGE_DIAGNOSTICS,
    diagnose_post_page,
)


class _FakePage:
    def __init__(self, state=None, raises=None):
        self._state = state
        self._raises = raises
        self.url = (state or {}).get("url", "")

    async def evaluate(self, _js):
        if self._raises:
            raise self._raises
        return self._state


def _diagnose(**state):
    state.setdefault("url", "https://www.linkedin.com/feed/update/urn:li:activity:1/")
    state.setdefault("loggedOut", False)
    state.setdefault("notFound", False)
    state.setdefault("hasActionBar", False)
    return asyncio.run(diagnose_post_page(_FakePage(state)))


def test_detects_session_expired():
    assert _diagnose(loggedOut=True, url="https://www.linkedin.com/login") == "session_expired"


def test_detects_post_not_found():
    assert _diagnose(notFound=True) == "post_not_found"


def test_detects_action_bar_absent_on_a_valid_page():
    assert _diagnose() == "action_bar_absent"


def test_detects_action_bar_present():
    assert _diagnose(hasActionBar=True) == "action_bar_present"


def test_session_expired_wins_over_not_found():
    assert _diagnose(loggedOut=True, notFound=True) == "session_expired"


def test_probe_failure_is_not_fatal():
    page = _FakePage(raises=RuntimeError("execution context destroyed"))
    assert asyncio.run(diagnose_post_page(page)) == "probe_failed"


def test_every_code_has_a_human_message():
    codes = {
        "session_expired",
        "post_not_found",
        "action_bar_absent",
        "action_bar_present",
        "probe_failed",
    }
    assert codes == set(POST_PAGE_DIAGNOSTICS)


if __name__ == "__main__":
    test_detects_session_expired()
    test_detects_post_not_found()
    test_detects_action_bar_absent_on_a_valid_page()
    test_detects_action_bar_present()
    test_session_expired_wins_over_not_found()
    test_probe_failure_is_not_fatal()
    test_every_code_has_a_human_message()
    print("✅ test_post_page_diag OK")
