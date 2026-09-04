"""LinkedIn repost (reshare) via REST Posts API."""
import json
import logging
import re
from typing import Optional

import httpx

from ..config.settings import settings
from .auth import LinkedInOAuth, TokenExpiredError
from .post import PostVisibility

logger = logging.getLogger(__name__)

ACTIVITY_ID_PATTERN = re.compile(r"urn:li:activity:(\d+)")
# /posts/author_slug-ACTIVITYID-SUFFIX/ or /feed/update/urn:li:activity:ID
_POSTS_URL_PATTERN = re.compile(r"/posts/[^/]+-(\d{16,})-[A-Za-z0-9]+/?")
COMPKEY_URN_PATTERN = re.compile(r"urn:li:compkey:(.+)", re.I)
_FULL_POST_URL_PATTERN = re.compile(r"^https?://(www\.)?linkedin\.com/(posts/|feed/update/)", re.I)
# Formes d'URN acceptées par /feed/update/ pour un même id numérique. LinkedIn
# n'en rend qu'une correctement selon la nature du post (post natif, repost,
# ugcPost) — d'où l'essai en cascade côté UI.
_POST_URN_FORMS = ("activity", "ugcPost", "share")


class RepostError(Exception):
    """Raised when repost creation fails."""


class RepostForbiddenError(RepostError):
    """API repost forbidden (403) — typically third-party posts on Share on LinkedIn."""


def is_repost_api_forbidden(exc: RepostError) -> bool:
    """True when the REST API refused the repost and UI fallback may work."""
    if isinstance(exc, RepostForbiddenError):
        return True
    return "403" in str(exc) or "Accès refusé (403)" in str(exc)


def activity_id_from_post_ref(post_ref: str) -> Optional[str]:
    """Extract the numeric activity id from a URL, URN, or bare id."""
    post_ref = post_ref.strip()
    match = ACTIVITY_ID_PATTERN.search(post_ref)
    if match:
        return match.group(1)
    match = _POSTS_URL_PATTERN.search(post_ref)
    if match:
        return match.group(1)
    if post_ref.isdigit():
        return post_ref
    return None


def post_url_candidates(post_ref: str) -> list[str]:
    """URLs de navigation à tenter, dans l'ordre, pour une action UI (like/repost).

    Le id numérique d'un permalien /posts/{slug}-{id}-{suffix}/ est un share ou
    ugcPost id, qui n'est PAS toujours un urn:li:activity: valide : reconstruire
    /feed/update/urn:li:activity:{id}/ à partir de là peut atterrir sur une page
    sans barre d'action, alors que le permalien d'origine se charge très bien.

    C'est exactement le mode d'échec du rapport du 2026-09-03 : appelé avec
    l'URN reconstruit depuis un slug `-share-`, like_post/repost_post ne
    trouvaient aucun bouton, tandis que l'URL /posts/ complète fonctionnait.

    D'où une cascade plutôt qu'une URL unique :
    1. l'URL fournie telle quelle si c'en est déjà une (elle est faite pour marcher) ;
    2. les trois formes d'URN sur /feed/update/ (activity, ugcPost, share) —
       seule la bonne rend la barre d'action.

    Limite assumée : un id numérique nu ne dit pas de quel type d'entité il
    relève, et les espaces d'ids activity / share / ugcPost sont distincts. Rien
    ne garantit donc formellement que les trois formes désignent le même post.
    Cette ambiguïté est inhérente à l'entrée, pas à la cascade (l'ancien code la
    portait déjà en ne tentant que la forme activity). Pour une action d'écriture
    sûre, passer le permalien complet (`linkedin_url` de scrape_feed) plutôt
    qu'un URN reconstruit : il est alors tenté en premier et lève l'ambiguïté.
    """
    ref = post_ref.strip()
    candidates: list[str] = []

    if _FULL_POST_URL_PATTERN.match(ref):
        candidates.append(ref.split("?")[0].split("#")[0])

    activity_id = activity_id_from_post_ref(ref)
    if activity_id:
        for urn_form in _POST_URN_FORMS:
            candidates.append(
                f"https://www.linkedin.com/feed/update/urn:li:{urn_form}:{activity_id}/"
            )

    return list(dict.fromkeys(candidates))


def canonical_post_url(post_ref: str) -> Optional[str]:
    """Première URL à tenter pour une action UI (voir post_url_candidates)."""
    candidates = post_url_candidates(post_ref)
    return candidates[0] if candidates else None


def compkey_from_post_ref(post_ref: str) -> Optional[str]:
    """Extract componentkey prefix from urn:li:compkey:… for feed card lookup."""
    post_ref = post_ref.strip()
    match = COMPKEY_URN_PATTERN.search(post_ref)
    if not match:
        return None
    raw = match.group(1)
    base = raw.replace("expanded", "").split("FeedType_")[0]
    return base if len(base) >= 16 else raw


def parent_urn_candidates(activity_id: str) -> list[str]:
    """URN formats to try for reshareContext.parent (API expects share or ugcPost)."""
    return [
        f"urn:li:share:{activity_id}",
        f"urn:li:ugcPost:{activity_id}",
    ]


class RepostManager:
    """Manager for LinkedIn reposts via POST /rest/posts."""

    def __init__(self, auth_client: LinkedInOAuth) -> None:
        self.auth_client = auth_client

    @property
    def _headers(self) -> dict:
        if not self.auth_client.access_token:
            raise RepostError("Non authentifié, lance authenticate d'abord")

        return {
            "Authorization": f"Bearer {self.auth_client.access_token}",
            "X-Restli-Protocol-Version": settings.RESTLI_PROTOCOL_VERSION,
            "LinkedIn-Version": settings.LINKEDIN_REST_VERSION,
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        parent_urn: str,
        commentary: str,
        visibility: PostVisibility,
    ) -> dict:
        if not self.auth_client.user_id:
            raise RepostError(
                "Profil utilisateur inconnu. Relance authenticate pour charger userinfo."
            )

        payload: dict = {
            "author": f"urn:li:person:{self.auth_client.user_id}",
            "commentary": commentary,
            "visibility": visibility.value,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "reshareContext": {"parent": parent_urn},
        }
        return payload

    async def repost(
        self,
        post_ref: str,
        commentary: str = "",
        visibility: PostVisibility = PostVisibility.PUBLIC,
        *,
        dry_run: bool = False,
    ) -> str:
        """Repost a LinkedIn post by URL, activity URN, or numeric activity id.

        Returns:
            Created repost id (x-restli-id header or response body id).
        """
        activity_id = activity_id_from_post_ref(post_ref)
        if not activity_id:
            raise RepostError(
                f"Impossible d'extraire urn:li:activity:ID depuis : {post_ref!r}"
            )

        candidates = parent_urn_candidates(activity_id)
        if dry_run:
            preview = {
                "activity_id": activity_id,
                "parent_candidates": candidates,
                "payload_example": self._build_payload(
                    candidates[0], commentary, visibility
                ),
                "endpoint": str(settings.LINKEDIN_REST_POSTS_URL),
                "linkedin_version": settings.LINKEDIN_REST_VERSION,
            }
            return json.dumps(preview, indent=2, ensure_ascii=False)

        last_error: Optional[str] = None
        async with httpx.AsyncClient() as client:
            for parent_urn in candidates:
                payload = self._build_payload(parent_urn, commentary, visibility)
                logger.info("Tentative repost parent=%s", parent_urn)
                response = await client.post(
                    str(settings.LINKEDIN_REST_POSTS_URL),
                    headers=self._headers,
                    json=payload,
                )

                if response.status_code == 201:
                    post_id = response.headers.get("x-restli-id")
                    if not post_id:
                        try:
                            body = response.json()
                            post_id = body.get("id")
                        except json.JSONDecodeError:
                            post_id = None
                    if not post_id:
                        post_id = "unknown"
                    logger.info("Repost créé : %s (parent=%s)", post_id, parent_urn)
                    return post_id

                last_error = f"{response.status_code}: {response.text}"
                logger.warning("Repost échoué pour %s — %s", parent_urn, last_error)

                if response.status_code == 401:
                    raise TokenExpiredError(
                        "Repost API : token OAuth rejeté (401). "
                        "Relance authenticate, ou laisse le fallback Playwright agir."
                    )
                if response.status_code == 403:
                    raise RepostForbiddenError(
                        "Accès refusé (403). L'API Share on LinkedIn ne permet souvent pas "
                        "de reposter le post d'un tiers — fallback Playwright disponible."
                    )
                if response.status_code not in (400, 404, 422):
                    raise RepostError(f"Erreur API repost : {last_error}")

        raise RepostError(
            f"Aucun format parent URN n'a fonctionné pour activity:{activity_id}. "
            f"Dernière erreur : {last_error}"
        )
