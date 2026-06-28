"""Repost LinkedIn posts via Playwright UI (fallback when API returns 403)."""
import logging
import re

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .repost import activity_id_from_post_ref

logger = logging.getLogger(__name__)

CLICK_REPOST_ON_PAGE_JS = """
() => {
  function isPostRepostBtn(b) {
    var a = (b.getAttribute("aria-label") || "").trim();
    if (a !== "Republier" && a !== "Repost") return false;
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

CLICK_INSTANT_REPOST_MENU_JS = """
() => {
  var nodes = Array.from(document.querySelectorAll('div[role="button"], li[role="menuitem"]'));
  for (var i = 0; i < nodes.length; i++) {
    var t = (nodes[i].innerText || "").trim();
    if (/Diffusez instantan/i.test(t) || /Instantly repost/i.test(t)) {
      nodes[i].click();
      return { clicked: true, via: "instant_menu" };
    }
  }
  for (var j = 0; j < nodes.length; j++) {
    var text = (nodes[j].innerText || "").trim();
    var firstLine = text.split(String.fromCharCode(10))[0];
    if (
      (firstLine === "Republier" || firstLine === "Repost") &&
      /instant|instantan/i.test(text)
    ) {
      nodes[j].click();
      return { clicked: true, via: firstLine };
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
    """Return a feed update URL from a post URL, URN, or activity id."""
    activity_id = activity_id_from_post_ref(post_ref)
    if not activity_id:
        raise RepostUIError(
            f"URL ou URN invalide (activity id introuvable) : {post_ref!r}"
        )
    return (
        f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"
    )


class RepostUI:
    """Repost via LinkedIn web UI."""

    def __init__(self, page: Page) -> None:
        self.page = page

    async def repost(self, post_ref: str, commentary: str = "") -> str:
        """Open post page and republish via Republier button."""
        post_url = normalize_post_url(post_ref)
        logger.info("Repost UI : %s", post_url)

        await self.page.goto(post_url, wait_until="domcontentloaded", timeout=45000)
        await self.page.wait_for_timeout(3500)

        if not await self.page.evaluate(CLICK_REPOST_ON_PAGE_JS):
            raise RepostUIError(
                "Bouton Republier introuvable sur la page du post "
                "(reposts désactivés ou session expirée ?)."
            )
        await self.page.wait_for_timeout(1500)

        if commentary.strip():
            result = await self._repost_with_commentary(commentary)
        else:
            result = await self._repost_instant()

        if not await self._verify_repost_published():
            raise RepostUIError(
                "Le clic repost a été effectué mais aucune confirmation LinkedIn "
                "n'a été détectée — le repost n'a probablement pas été publié."
            )
        return result

    async def _repost_instant(self) -> str:
        confirm = await self.page.evaluate(CLICK_INSTANT_REPOST_MENU_JS)
        if not confirm.get("clicked"):
            confirm = await self._click_instant_via_locator()
        if not confirm.get("clicked"):
            raise RepostUIError(
                "Option « Diffusez instantanément » introuvable dans le menu Republier. "
                "LinkedIn a peut-être changé l'interface."
            )
        await self.page.wait_for_timeout(2500)
        via = confirm.get("via", "instant")
        logger.info("Repost UI instant confirmé via %s", via)
        return f"Repost publié via Playwright (sans commentaire)."

    async def _click_instant_via_locator(self) -> dict:
        patterns = (
            r"Diffusez instantan",
            r"Instantly repost",
        )
        for pattern in patterns:
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
        return "Repost publié via Playwright (avec commentaire)."

    async def _verify_repost_published(self) -> bool:
        """Best-effort check that LinkedIn acknowledged the repost."""
        toast_selectors = (
            ".artdeco-toast-item",
            "[data-test-artdeco-toast-item-type]",
            ".artdeco-toast-item__message",
        )
        for selector in toast_selectors:
            try:
                await self.page.wait_for_selector(selector, timeout=8000)
                text = await self.page.locator(selector).first.inner_text()
                logger.info("Toast repost : %s", text[:120])
                if re.search(r"repub|repost|success|réussi|publi", text, re.I):
                    return True
            except PlaywrightTimeoutError:
                continue

        # Menu instantané disparu = bon signe si pas d'erreur visible
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
