"""Diagnostic de l'état d'une page post LinkedIn avant de conclure à un échec.

Contexte (rapport du 2026-09-03) : `like_post` / `repost_post` renvoyaient
« Bouton introuvable — le post n'existe peut-être plus ou la session a expiré »
dans tous les cas de bouton absent. Or le post existait, la session était
valide, et la même action réussissait 30 s plus tard avec une autre forme
d'URL : le message envoyait sur de fausses pistes.

Ce module sonde la page pour distinguer les causes réelles, afin que l'appelant
sache s'il doit abandonner (session expirée), tenter une autre forme d'URL
(post introuvable / barre d'action absente) ou chercher ailleurs (la barre
d'action est là, donc c'est le sélecteur du bouton qui ne matche plus).
"""
import logging

logger = logging.getLogger(__name__)

# Codes de diagnostic → message lisible, repris dans les erreurs remontées au
# client MCP. Un code par cause distincte, jamais un fourre-tout.
POST_PAGE_DIAGNOSTICS = {
    "session_expired": (
        "session expirée (redirection vers login/authwall) — relance "
        "create_scrape_session"
    ),
    "post_not_found": "page de post indisponible (supprimé, privé ou URL invalide)",
    "action_bar_absent": (
        "page chargée mais sans barre d'action (vue partielle ou rendu non hydraté)"
    ),
    "action_bar_present": (
        "barre d'action présente mais le bouton n'a pas été trouvé — sélecteur "
        "probablement obsolète côté LinkedIn"
    ),
    "probe_failed": "état de la page indéterminé (sonde en échec)",
}

# Sonde exécutée dans la page : URL courante, mur de login, page indisponible,
# présence d'une barre d'action (J'aime / Republier), en FR et EN.
POST_PAGE_STATE_JS = """
() => {
  var url = location.href;
  var text = ((document.body && document.body.innerText) || "").slice(0, 4000);
  var loggedOut = /\\/(login|authwall|checkpoint|uas\\/login)/i.test(url) ||
                  !!document.querySelector('input[name="session_key"]');
  var notFoundRe = new RegExp(
    "page introuvable|post introuvable|contenu introuvable|" +
    "n'est plus disponible|n'est pas disponible|page n'existe pas|" +
    "page doesn't exist|isn't available|no longer available|content not found",
    "i"
  );
  var notFound = notFoundRe.test(text);
  var hasActionBar = Array.from(document.querySelectorAll("button")).some(function (b) {
    var t = (b.innerText || "").trim();
    if (t === "J'aime" || t === "Like" || t === "Republier" || t === "Repost") return true;
    var a = (b.getAttribute("aria-label") || "").trim();
    return /^(Republier|Repost)\\b/i.test(a) ||
           /bouton de r\\u00e9action|reaction button/i.test(a);
  });
  return { url: url, loggedOut: loggedOut, notFound: notFound, hasActionBar: hasActionBar };
}
"""


async def diagnose_post_page(page) -> str:
    """Code de `POST_PAGE_DIAGNOSTICS` décrivant l'état de la page courante.

    Ne lève jamais : une sonde en échec vaut `probe_failed`, l'appelant reste
    libre de poursuivre sa cascade d'URL.
    """
    try:
        state = await page.evaluate(POST_PAGE_STATE_JS)
    except Exception as exc:  # sonde best-effort, ne doit pas masquer l'échec réel
        logger.debug("Sonde d'état de page post en échec : %s", exc)
        return "probe_failed"

    if not isinstance(state, dict):
        return "probe_failed"
    if state.get("loggedOut"):
        return "session_expired"
    if state.get("notFound"):
        return "post_not_found"
    if state.get("hasActionBar"):
        return "action_bar_present"
    return "action_bar_absent"


def describe_diagnostic(code: str) -> str:
    """Message lisible pour un code de diagnostic."""
    return POST_PAGE_DIAGNOSTICS.get(code, code)
