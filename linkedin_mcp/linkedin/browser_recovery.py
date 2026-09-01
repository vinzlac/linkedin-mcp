"""Détection des navigateurs/pages Playwright crashés et navigation défensive.

Un navigateur Chromium partagé (réutilisé entre ``scrape_feed`` et les outils
d'écriture) peut mourir sans que ce serveur en soit informé : ``Page crashed``
(souvent après un OOM ou un usage prolongé) laisse la page ``is_connected()`` et
non ``is_closed()``, si bien que chaque ``page.goto`` suivant échoue en boucle.
Ce module fournit :

- ``is_recoverable_browser_error`` : reconnaît ces erreurs pour déclencher une
  relance propre + un nouvel essai.
- ``safe_goto`` : ``goto`` suivi d'une attente d'état de chargement, pour éviter
  ``Execution context was destroyed, most likely because of a navigation`` quand
  un ``page.evaluate`` part trop tôt après une redirection LinkedIn.
"""
import logging

from playwright.async_api import Error as PlaywrightError

logger = logging.getLogger(__name__)

# Fragments de messages d'erreur Playwright signifiant que le navigateur / la
# page / le contexte courant est inutilisable : la seule issue est une nouvelle
# instance de navigateur. Comparaison sur la chaîne car une page crashée ne se
# distingue pas via l'API (is_connected() reste True, is_closed() reste False).
_RECOVERABLE_ERROR_MARKERS = (
    "page crashed",
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "browser has been disconnected",
    "browser has disconnected",
    "connection closed",
    "websocket error",
    "execution context was destroyed",
)


def is_recoverable_browser_error(exc: BaseException) -> bool:
    """True si l'erreur traduit un navigateur/page mort qu'une relance corrige."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _RECOVERABLE_ERROR_MARKERS)


async def safe_goto(
    page,
    url: str,
    *,
    timeout: int = 45000,
    settle_ms: int = 3000,
) -> None:
    """Navigue vers ``url`` puis attend la fin de navigation avant tout evaluate.

    LinkedIn renvoie fréquemment un ``goto`` frais vers une page intermédiaire /
    une redirection ; un ``page.evaluate`` déclenché trop tôt meurt alors avec
    « Execution context was destroyed, most likely because of a navigation ».
    Attendre l'état ``load`` après le ``goto`` (et non un simple sleep fixe)
    referme cette fenêtre.
    """
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    try:
        await page.wait_for_load_state("load", timeout=timeout)
    except PlaywrightError as exc:
        # networkidle/load jamais atteint (feed LinkedIn = polling permanent) :
        # non bloquant, le settle ci-dessous + les retries appelants suffisent.
        logger.debug("wait_for_load_state('load') ignoré après goto %s : %s", url, exc)
    if settle_ms:
        await page.wait_for_timeout(settle_ms)
