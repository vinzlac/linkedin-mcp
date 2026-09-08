#!/usr/bin/env python3
"""Détection du bouton J'aime sur le rendu LinkedIn de septembre 2026.

Constaté en production le 2026-09-07 sur une vraie page post : le bouton existe
mais son libellé a changé, et `like_post` ne le trouvait plus.

    attendu par le code : aria-label « État du bouton de réaction : aucune réaction »
    réalité             : texte « J’aime », aria-pressed="false",
                          aria-label « Réagir avec “J’aime” »

Danger principal : chaque COMMENTAIRE porte son propre bouton au libellé presque
identique (« Réagir avec “J’aime” au commentaire de X »). Un sélecteur permissif
likerait un commentaire à la place du post — une écriture fausse et silencieuse
sur le compte de l'utilisateur.

Usage:
    uv run python test_like_button_dom.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from linkedin_mcp.linkedin.like_ui import CLICK_LIKE_ON_PAGE_JS

# Le bouton du post d'abord, puis ceux de deux commentaires — ordre réel du DOM.
POST_WITH_COMMENTS = """
<html><body>
  <button aria-pressed="false" aria-label="Réagir avec “J’aime”" id="post-like">J’aime</button>
  <button aria-label="Commenter">Commenter</button>
  <button aria-pressed="false" aria-label="Réagir avec “J’aime” au commentaire de  Pramoda Sahu" id="c1">J’aime</button>
  <button aria-pressed="false" aria-label="Réagir avec “J’aime” au commentaire de  Weipeng Zhuo" id="c2">J’aime</button>
</body></html>
"""

ALREADY_LIKED = """
<html><body>
  <button aria-pressed="true" aria-label="Réagir avec “J’aime”" id="post-like">J’aime</button>
</body></html>
"""

# Ancien rendu — doit continuer de fonctionner tant que des surfaces n'ont pas migré.
LEGACY = """
<html><body>
  <button aria-label="État du bouton de réaction : aucune réaction" id="post-like">J'aime</button>
</body></html>
"""

NO_LIKE_BUTTON = """<html><body><button aria-label="Commenter">Commenter</button></body></html>"""

ONLY_COMMENT_LIKES = """
<html><body>
  <button aria-pressed="false" aria-label="Réagir avec “J’aime” au commentaire de  Alice">J’aime</button>
</body></html>
"""


async def _run(html):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html)
            result = await page.evaluate(CLICK_LIKE_ON_PAGE_JS)
            clicked_id = await page.evaluate(
                "() => window.__clicked_id || null"
            )
            return result, clicked_id
        finally:
            await browser.close()


def _instrument(html):
    """Trace l'id du bouton réellement cliqué, sans rien envoyer à LinkedIn."""
    return html.replace(
        "</body>",
        "<script>document.addEventListener('click', function (e) {"
        " window.__clicked_id = e.target.closest('button') ? e.target.closest('button').id : null;"
        "}, true);</script></body>",
    )


def test_likes_the_post_and_never_a_comment():
    result, clicked = asyncio.run(_run(_instrument(POST_WITH_COMMENTS)))
    assert result.get("clicked") is True, result
    assert clicked == "post-like", f"bouton cliqué : {clicked}"


def test_detects_an_already_liked_post():
    result, _ = asyncio.run(_run(ALREADY_LIKED))
    assert result.get("status") == "already_liked", result


def test_still_supports_the_legacy_rendering():
    result, clicked = asyncio.run(_run(_instrument(LEGACY)))
    assert result.get("clicked") is True, result
    assert clicked == "post-like"


def test_reports_not_found_rather_than_guessing():
    result, _ = asyncio.run(_run(NO_LIKE_BUTTON))
    assert result.get("status") == "button_not_found", result


def test_never_falls_back_to_a_comment_button():
    """Sans bouton de post, on n'agit pas — un échec vaut mieux qu'un like faux."""
    result, clicked = asyncio.run(_run(_instrument(ONLY_COMMENT_LIKES)))
    assert result.get("status") == "button_not_found", result
    assert clicked is None, f"un commentaire a été liké : {clicked}"


if __name__ == "__main__":
    test_likes_the_post_and_never_a_comment()
    test_detects_an_already_liked_post()
    test_still_supports_the_legacy_rendering()
    test_reports_not_found_rather_than_guessing()
    test_never_falls_back_to_a_comment_button()
    print("✅ test_like_button_dom OK")
