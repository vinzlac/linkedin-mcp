"""Défilement du feed LinkedIn, quel que soit le conteneur qui scrolle.

Constat du 2026-09-03 (inspection du feed live) : le nouveau rendu ne scrolle
plus la fenêtre mais un conteneur interne — `document.scrollHeight` est égal à
`clientHeight`, et c'est `<main>` qui porte le débordement. Tous les
`window.scrollBy(...)` des chemins like/repost étaient donc devenus des no-op :
le fallback « carte feed » (celui qu'on emprunte pour un `urn:li:compkey:`) ne
pouvait plus amener dans le viewport une carte située plus bas.

On ne code pas en dur `<main>` pour autant : la cible est déterminée à
l'exécution, en repartant du document puis en cherchant le conteneur défilable
le plus grand. Un changement de rendu côté LinkedIn ne rendra donc pas ce
module muet.
"""
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)

SCROLL_FEED_JS = r"""
(dy) => {
  var MARGIN = 50;
  function scrollable(el) {
    return !!el && el.scrollHeight > el.clientHeight + MARGIN;
  }

  var doc = document.scrollingElement || document.documentElement;
  if (scrollable(doc)) {
    window.scrollBy(0, dy);
    return "window";
  }

  var main = document.querySelector("main");
  if (scrollable(main)) {
    main.scrollTop += dy;
    return "main";
  }

  var roleMain = document.querySelector('[role="main"]');
  if (scrollable(roleMain)) {
    roleMain.scrollTop += dy;
    return "role-main";
  }

  // Repli : le plus grand conteneur défilable de la page.
  var best = null;
  var candidates = document.querySelectorAll("div, section, ul");
  for (var i = 0; i < candidates.length; i++) {
    var el = candidates[i];
    if (el.clientHeight < 300 || !scrollable(el)) continue;
    if (!best || el.clientHeight > best.clientHeight) best = el;
  }
  if (best) {
    best.scrollTop += dy;
    return "container";
  }

  return "none";
}
"""


async def scroll_feed(page: Page, dy: int) -> str:
    """Fait défiler le feed de ``dy`` pixels et renvoie la stratégie retenue.

    Returns:
        ``"window"``, ``"main"``, ``"role-main"``, ``"container"`` selon
        l'élément effectivement défilé, ou ``"none"`` si rien n'était défilable.
    """
    strategy = await page.evaluate(SCROLL_FEED_JS, dy)
    if strategy == "none":
        logger.debug("Aucun conteneur défilable trouvé pour dy=%s", dy)
    else:
        logger.debug("Feed défilé de %s px via %s", dy, strategy)
    return strategy
