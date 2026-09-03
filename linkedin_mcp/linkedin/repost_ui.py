"""Repost LinkedIn posts via Playwright UI (fallback when API returns 403)."""
import logging
import re

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from linkedin_scraper.core import check_cooldown, enforce_write_action_pacing
from linkedin_scraper.scrapers.feed import FEED_URL, _WAIT_FOR_FEED_JS

from .browser_recovery import safe_goto
from .post_page import describe_diagnostic, diagnose_post_page
from .repost import (
    activity_id_from_post_ref,
    canonical_post_url,
    compkey_from_post_ref,
    post_url_candidates,
)

logger = logging.getLogger(__name__)

CLICK_REPOST_ON_PAGE_JS = """
() => {
  function isPostRepostBtn(b) {
    var a = (b.getAttribute("aria-label") || "").trim();
    var t = (b.innerText || "").trim();
    // LinkedIn dropped aria-label from this button (2026-08) — the button is
    // still identifiable by its visible text or the repost SVG icon.
    var matches = a === "Republier" || a === "Repost"
      || t === "Republier" || t === "Repost"
      || !!b.querySelector("svg#repost-small");
    if (!matches) return false;
    if (b.closest(".comments-comment-item, .comments-comments-list, .comment-item")) {
      return false;
    }
    return true;
  }
  var btns = Array.from(document.querySelectorAll("button")).filter(isPostRepostBtn);
  if (btns.length === 0) return false;
  btns[0].click();
  return true;
}
"""

CLICK_REPOST_IN_FEED_CARD_JS = """
({ mode, value }) => {
  function isPostRepostBtn(b) {
    var a = (b.getAttribute("aria-label") || "").trim();
    var t = (b.innerText || "").trim();
    var matches = a === "Republier" || a === "Repost"
      || t === "Republier" || t === "Repost"
      || !!b.querySelector("svg#repost-small");
    if (!matches) return false;
    if (b.closest(".comments-comment-item, .comments-comments-list, .comment-item")) {
      return false;
    }
    return true;
  }
  function cardRoot(el) {
    return el.closest("div[data-urn], article, .feed-shared-update-v2") || el;
  }
  var card = null;
  if (mode === "compkey") {
    var el = document.querySelector('[componentkey*="' + value + '"]');
    if (el) card = cardRoot(el);
  } else if (mode === "activity") {
    var nodes = document.querySelectorAll("div[data-urn], article, [componentkey]");
    for (var i = 0; i < nodes.length; i++) {
      var html = nodes[i].innerHTML || "";
      var urn = nodes[i].getAttribute("data-urn") || "";
      if (html.indexOf(value) !== -1 || urn.indexOf(value) !== -1) {
        card = cardRoot(nodes[i]);
        break;
      }
    }
  }
  if (!card) return { clicked: false, reason: "card_not_found" };
  var btns = Array.from(card.querySelectorAll("button")).filter(isPostRepostBtn);
  if (!btns.length) return { clicked: false, reason: "no_repost_button" };
  btns[0].click();
  return { clicked: true };
}
"""

CLICK_INSTANT_REPOST_MENU_JS = """
() => {
  // LinkedIn has used several wordings for the same "instant repost" menu
  // item over time ("Diffusez instantanément", "Republier instantanément",
  // "Instantly repost", "Repost now"...) — match on the "instant(ly)" keyword
  // rather than an exact phrase so wording changes don't silently break this.
  var nodes = Array.from(document.querySelectorAll('div[role="button"], li[role="menuitem"]'));
  for (var i = 0; i < nodes.length; i++) {
    var t = (nodes[i].innerText || "").trim();
    if (/instantan/i.test(t) || /instantly/i.test(t)) {
      nodes[i].click();
      return { clicked: true, via: t.slice(0, 40) };
    }
  }
  return { clicked: false };
}
"""

OPEN_WITH_COMMENTARY_JS = """
() => {
  var nodes = Array.from(document.querySelectorAll('div[role="button"], li[role="menuitem"]'));
  for (var i = 0; i < nodes.length; i++) {
    var t = (nodes[i].innerText || "").trim();
    if (/donnant votre avis|with your thoughts|with thoughts/i.test(t)) {
      nodes[i].click();
      return { opened: true };
    }
  }
  return { opened: false };
}
"""

SUBMIT_COMMENTARY_JS = """
() => {
  var labels = ["Publier", "Post", "Republier", "Repost", "Partager", "Share"];
  var nodes = Array.from(document.querySelectorAll("button, div[role='button']"));
  for (var li = 0; li < labels.length; li++) {
    var label = labels[li];
    for (var i = 0; i < nodes.length; i++) {
      var t = (nodes[i].innerText || "").trim();
      if (t === label) {
        nodes[i].click();
        return { submitted: true, via: label };
      }
    }
  }
  return { submitted: false };
}
"""


class RepostUIError(Exception):
    """Raised when Playwright repost fails."""


def normalize_post_url(post_ref: str) -> str:
    """Return a navigable URL from a post URL, URN, or activity id."""
    url = canonical_post_url(post_ref)
    if not url:
        raise RepostUIError(
            f"URL ou URN invalide (activity id introuvable) : {post_ref!r}"
        )
    return url


class RepostUI:
    """Repost via LinkedIn web UI."""

    def __init__(self, page: Page) -> None:
        self.page = page

    async def repost(self, post_ref: str, commentary: str = "") -> str:
        """Repost via post page (activity URL) or feed card (compkey)."""
        check_cooldown()
        await enforce_write_action_pacing("write_action")

        activity_id = activity_id_from_post_ref(post_ref)
        if activity_id:
            return await self._repost_via_post_page(post_ref, commentary)

        compkey = compkey_from_post_ref(post_ref)
        if compkey:
            return await self.repost_feed_card(post_ref, commentary)

        raise RepostUIError(
            f"Référence post non reconnue (activity ou compkey attendu) : {post_ref!r}"
        )

    async def _repost_via_post_page(self, post_ref: str, commentary: str) -> str:
        """Repost via la page du post, en essayant chaque forme d'URL en cascade.

        Même cause qu'un like en échec sur un URN reconstruit : l'id d'un slug
        `-share-` / `-ugcPost-` n'est pas forcément un activity id valide, donc
        /feed/update/urn:li:activity:<id>/ peut rendre une page sans bouton
        Republier (rapport du 2026-09-03). Voir post_url_candidates().
        """
        candidates = post_url_candidates(post_ref)
        if not candidates:
            raise RepostUIError(
                f"URL ou URN invalide (activity id introuvable) : {post_ref!r}"
            )

        diagnostics: list[str] = []
        for post_url in candidates:
            logger.info("Repost UI (page post) : %s", post_url)

            # Skip the navigation if we're already on that exact post page (e.g.
            # a prior scrape_post call landed us there) — re-navigating here was
            # what pushed failures into a multi-minute feed-card fallback tail.
            current = self.page.url or ""
            if post_url.rstrip("/") not in current.rstrip("/"):
                await safe_goto(self.page, post_url, settle_ms=3500)
            else:
                logger.info("Déjà sur la page post, pas de nouvelle navigation")

            clicked = False
            for attempt, wait_ms in enumerate((0, 1500, 2500)):
                if wait_ms:
                    await self.page.wait_for_timeout(wait_ms)
                clicked = await self.page.evaluate(CLICK_REPOST_ON_PAGE_JS)
                if clicked:
                    break
                logger.info("Bouton Republier introuvable sur page post (essai %s/3)", attempt + 1)

            if clicked:
                await self.page.wait_for_timeout(1500)
                return await self._complete_repost(commentary, via="page post")

            code = await diagnose_post_page(self.page)
            diagnostics.append(f"{post_url} → {describe_diagnostic(code)}")
            logger.info("Repost KO sur %s : %s", post_url, code)
            if code == "session_expired":
                break

        raise RepostUIError(
            "Bouton Republier introuvable sur la page post. Diagnostic par URL "
            "tentée : " + " | ".join(diagnostics)
        )

    async def repost_feed_card(self, post_ref: str, commentary: str = "") -> str:
        """Repost by clicking Republier on a visible feed card (compkey-friendly)."""
        compkey = compkey_from_post_ref(post_ref)
        activity_id = activity_id_from_post_ref(post_ref)
        if not compkey and not activity_id:
            raise RepostUIError(
                f"Impossible de cibler une carte feed depuis : {post_ref!r}"
            )

        mode = "compkey" if compkey else "activity"
        value = compkey if compkey else activity_id
        logger.info("Repost UI (carte feed) mode=%s", mode)

        await self._ensure_feed_loaded()

        clicked = None
        for scroll_step in (0, 800, 1200, 1600, 2000):
            if scroll_step:
                await self.page.evaluate(f"window.scrollBy(0, {scroll_step})")
                await self.page.wait_for_timeout(1500)
            clicked = await self.page.evaluate(
                CLICK_REPOST_IN_FEED_CARD_JS, {"mode": mode, "value": value}
            )
            if clicked.get("clicked"):
                break

        if not clicked or not clicked.get("clicked"):
            reason = (clicked or {}).get("reason", "unknown")
            raise RepostUIError(
                f"Bouton Republier introuvable sur la carte feed ({reason}). "
                "Le post est peut-être hors écran ou reposts désactivés."
            )

        await self.page.wait_for_timeout(1500)
        return await self._complete_repost(commentary, via="carte feed")

    async def _ensure_feed_loaded(self) -> None:
        """Navigate to the feed only if not already there (compkeys change on reload)."""
        current = self.page.url or ""
        already_on_feed = "/feed" in current and "update" not in current
        if not already_on_feed:
            await safe_goto(self.page, FEED_URL)
            await self.page.evaluate("window.scrollBy(0, 600)")
            await self.page.wait_for_timeout(2000)
        try:
            await self.page.wait_for_function(_WAIT_FOR_FEED_JS, timeout=40000)
        except PlaywrightTimeoutError as exc:
            raise RepostUIError(
                "Feed LinkedIn non chargé (session expirée ?). "
                "Relance create_scrape_session."
            ) from exc

    async def _complete_repost(self, commentary: str, *, via: str) -> str:
        if commentary.strip():
            result = await self._repost_with_commentary(commentary)
        else:
            result = await self._repost_instant()

        if not await self._verify_repost_published():
            raise RepostUIError(
                "Le clic repost a été effectué mais aucune confirmation LinkedIn "
                "n'a été détectée — le repost n'a probablement pas été publié."
            )
        return f"{result} ({via})"

    async def _repost_instant(self) -> str:
        confirm = await self.page.evaluate(CLICK_INSTANT_REPOST_MENU_JS)
        if not confirm.get("clicked"):
            confirm = await self._click_instant_via_locator()
        if not confirm.get("clicked"):
            raise RepostUIError(
                "Option de repost instantané introuvable dans le menu Republier. "
                "LinkedIn a peut-être changé l'interface."
            )
        await self.page.wait_for_timeout(2500)
        logger.info("Repost UI instant confirmé via %s", confirm.get("via"))
        return "Repost publié via Playwright (sans commentaire)"

    async def _click_instant_via_locator(self) -> dict:
        for pattern in (r"instantan", r"instantly"):
            loc = self.page.locator("div[role='button']").filter(
                has_text=re.compile(pattern, re.I)
            )
            if await loc.count() > 0:
                await loc.first.click()
                return {"clicked": True, "via": pattern}
        return {"clicked": False}

    async def _repost_with_commentary(self, commentary: str) -> str:
        opened = await self.page.evaluate(OPEN_WITH_COMMENTARY_JS)
        if not opened.get("opened"):
            loc = self.page.locator("div[role='button']").filter(
                has_text=re.compile(r"donnant votre avis|with your thoughts", re.I)
            )
            if await loc.count() > 0:
                await loc.first.click()
                opened = {"opened": True}

        if not opened.get("opened"):
            raise RepostUIError(
                "Option « Republier avec commentaire » introuvable."
            )

        await self.page.wait_for_timeout(1000)
        editor = self.page.locator(
            "div.ql-editor[contenteditable='true'], "
            "div[contenteditable='true'][role='textbox'], "
            "textarea"
        ).first
        if await editor.count() == 0:
            raise RepostUIError("Zone de texte du repost introuvable.")
        await editor.click()
        await editor.fill(commentary)

        submitted = await self.page.evaluate(SUBMIT_COMMENTARY_JS)
        if not submitted.get("submitted"):
            for name in ("Publier", "Post", "Republier", "Repost"):
                loc = self.page.get_by_role(
                    "button", name=re.compile(f"^{name}$", re.I)
                )
                if await loc.count() > 0:
                    await loc.first.click()
                    submitted = {"submitted": True, "via": name}
                    break

        if not submitted.get("submitted"):
            raise RepostUIError("Impossible de publier le repost avec commentaire.")

        await self.page.wait_for_timeout(2500)
        return "Repost publié via Playwright (avec commentaire)"

    async def _verify_repost_published(self) -> bool:
        for selector in (
            ".artdeco-toast-item",
            "[data-test-artdeco-toast-item-type]",
            ".artdeco-toast-item__message",
        ):
            try:
                await self.page.wait_for_selector(selector, timeout=8000)
                text = await self.page.locator(selector).first.inner_text()
                logger.info("Toast repost : %s", text[:120])
                if re.search(r"repub|repost|success|réussi|publi", text, re.I):
                    return True
            except PlaywrightTimeoutError:
                continue

        menu_open = await self.page.locator("div[role='button']").filter(
            has_text=re.compile(r"Diffusez instantan", re.I)
        ).count()
        if menu_open == 0:
            error = self.page.locator(".artdeco-inline-feedback--error")
            if await error.count() == 0:
                logger.warning(
                    "Pas de toast repost détecté ; confirmation faible uniquement"
                )
                return True

        return False
