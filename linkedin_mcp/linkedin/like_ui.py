"""Like LinkedIn posts via Playwright UI."""
import logging
import re

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from linkedin_scraper.scrapers.feed import FEED_URL, _WAIT_FOR_FEED_JS

from .repost import activity_id_from_post_ref, compkey_from_post_ref

logger = logging.getLogger(__name__)

# Matches the like button when the post is NOT yet liked (FR/EN)
_LIKE_BTN_PATTERN = re.compile(
    r"(État du bouton de réaction\s*[:\xa0]+\s*aucune réaction"
    r"|reaction button\s*[:\xa0]*\s*no reaction"
    r"|état.*aucune"
    r"|réaction.*aucune)",
    re.I,
)

# Matches the like button when already liked (to detect already-liked state)
_ALREADY_LIKED_PATTERN = re.compile(
    r"(État du bouton de réaction\s*[:\xa0]+\s*J.aime"
    r"|État du bouton de réaction\s*[:\xa0]+\s*Like"
    r"|reaction button\s*[:\xa0]*\s*like)",
    re.I,
)

CLICK_LIKE_ON_PAGE_JS = """
() => {
  var like_re = /État du bouton de réaction[\\s\\xa0]*[:\\xa0]+[\\s\\xa0]*aucune réaction/i;
  var liked_re = /État du bouton de réaction[\\s\\xa0]*[:\\xa0]+[\\s\\xa0]*(J.aime|Like)/i;
  var btns = Array.from(document.querySelectorAll("button"));
  for (var i = 0; i < btns.length; i++) {
    var a = (btns[i].getAttribute("aria-label") || "").trim();
    if (liked_re.test(a)) return { clicked: false, status: "already_liked" };
    if (like_re.test(a)) { btns[i].click(); return { clicked: true }; }
  }
  return { clicked: false, status: "button_not_found" };
}
"""

CLICK_LIKE_IN_CARD_JS = """
({ mode, value }) => {
  var like_re = /État du bouton de réaction[\\s\\xa0]*[:\\xa0]+[\\s\\xa0]*aucune réaction/i;
  var liked_re = /État du bouton de réaction[\\s\\xa0]*[:\\xa0]+[\\s\\xa0]*(J.aime|Like)/i;
  function cardRoot(el) {
    return el.closest("div[data-urn], article, .feed-shared-update-v2") || el;
  }
  var card = null;
  if (mode === "compkey") {
    var el = document.querySelector('[componentkey*="' + value + '"]');
    if (el) card = cardRoot(el);
  } else {
    var nodes = document.querySelectorAll("div[data-urn], article, [componentkey]");
    for (var i = 0; i < nodes.length; i++) {
      var urn = nodes[i].getAttribute("data-urn") || "";
      if (urn.indexOf(value) !== -1 || (nodes[i].innerHTML || "").indexOf(value) !== -1) {
        card = cardRoot(nodes[i]);
        break;
      }
    }
  }
  if (!card) return { clicked: false, status: "card_not_found" };
  var btns = Array.from(card.querySelectorAll("button"));
  for (var j = 0; j < btns.length; j++) {
    var a = (btns[j].getAttribute("aria-label") || "").trim();
    if (liked_re.test(a)) return { clicked: false, status: "already_liked" };
    if (like_re.test(a)) { btns[j].click(); return { clicked: true }; }
  }
  return { clicked: false, status: "button_not_found" };
}
"""


class LikeUIError(Exception):
    """Raised when Playwright like fails."""


class AlreadyLikedError(LikeUIError):
    """Post is already liked."""


def _post_url(post_ref: str) -> str:
    activity_id = activity_id_from_post_ref(post_ref)
    if not activity_id:
        raise LikeUIError(
            f"URL ou URN invalide (activity id introuvable) : {post_ref!r}"
        )
    return f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"


class LikeUI:
    """Like a LinkedIn post via Playwright."""

    def __init__(self, page: Page) -> None:
        self.page = page

    async def like(self, post_ref: str) -> str:
        """Like a post given its URL, activity URN, or compkey URN."""
        activity_id = activity_id_from_post_ref(post_ref)
        if activity_id:
            return await self._like_via_post_page(post_ref)

        compkey = compkey_from_post_ref(post_ref)
        if compkey:
            return await self._like_via_feed_card(post_ref)

        raise LikeUIError(
            f"Référence post non reconnue (activity ou compkey attendu) : {post_ref!r}"
        )

    async def _like_via_post_page(self, post_ref: str) -> str:
        post_url = _post_url(post_ref)
        logger.info("Like UI (page post) : %s", post_url)
        await self.page.goto(post_url, wait_until="domcontentloaded", timeout=45000)
        await self.page.wait_for_timeout(3000)

        result = await self.page.evaluate(CLICK_LIKE_ON_PAGE_JS)
        if result.get("status") == "already_liked":
            raise AlreadyLikedError("Ce post est déjà liké.")
        if not result.get("clicked"):
            logger.info("Bouton like absent sur page post, essai carte feed")
            return await self._like_via_feed_card(post_ref)

        await self.page.wait_for_timeout(1500)
        logger.info("Like publié (page post)")
        return "Post liké via Playwright (page post)."

    async def _like_via_feed_card(self, post_ref: str) -> str:
        compkey = compkey_from_post_ref(post_ref)
        activity_id = activity_id_from_post_ref(post_ref)
        if not compkey and not activity_id:
            raise LikeUIError(
                f"Impossible de cibler une carte feed depuis : {post_ref!r}"
            )

        mode = "compkey" if compkey else "activity"
        value = compkey if compkey else activity_id
        logger.info("Like UI (carte feed) mode=%s", mode)

        await self._ensure_feed_loaded()

        result = None
        for scroll_step in (0, 800, 1200, 1600, 2000):
            if scroll_step:
                await self.page.evaluate(f"window.scrollBy(0, {scroll_step})")
                await self.page.wait_for_timeout(1500)
            result = await self.page.evaluate(
                CLICK_LIKE_IN_CARD_JS, {"mode": mode, "value": value}
            )
            if result.get("clicked") or result.get("status") == "already_liked":
                break

        status = (result or {}).get("status", "unknown")
        if status == "already_liked":
            raise AlreadyLikedError("Ce post est déjà liké.")
        if not result or not result.get("clicked"):
            raise LikeUIError(
                f"Bouton J'aime introuvable sur la carte feed ({status}). "
                "Le post est peut-être hors écran ou la session a expiré."
            )

        await self.page.wait_for_timeout(1500)
        logger.info("Like publié (carte feed)")
        return "Post liké via Playwright (carte feed)."

    async def _ensure_feed_loaded(self) -> None:
        """Navigate to the feed only if not already there (compkeys change on reload)."""
        current = self.page.url or ""
        already_on_feed = "/feed" in current and "update" not in current
        if not already_on_feed:
            await self.page.goto(FEED_URL, wait_until="domcontentloaded", timeout=45000)
            await self.page.wait_for_timeout(3000)
            await self.page.evaluate("window.scrollBy(0, 600)")
            await self.page.wait_for_timeout(2000)
        try:
            await self.page.wait_for_function(_WAIT_FOR_FEED_JS, timeout=40000)
        except PlaywrightTimeoutError as exc:
            raise LikeUIError(
                "Feed LinkedIn non chargé (session expirée ?). "
                "Relance create_scrape_session."
            ) from exc
