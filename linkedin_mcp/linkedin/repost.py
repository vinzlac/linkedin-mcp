"""LinkedIn repost (reshare) via REST Posts API."""
import json
import logging
import re
from typing import Optional

import httpx

from ..config.settings import settings
from .auth import LinkedInOAuth
from .post import PostVisibility

logger = logging.getLogger(__name__)

ACTIVITY_ID_PATTERN = re.compile(r"urn:li:activity:(\d+)")
# /posts/author_slug-ACTIVITYID-SUFFIX/ or /feed/update/urn:li:activity:ID
_POSTS_URL_PATTERN = re.compile(r"/posts/[^/]+-(\d{16,})-[A-Za-z0-9]+/?")
COMPKEY_URN_PATTERN = re.compile(r"urn:li:compkey:(.+)", re.I)
_FULL_POST_URL_PATTERN = re.compile(r"^https?://(www\.)?linkedin\.com/(posts/|feed/update/)", re.I)


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


def canonical_post_url(post_ref: str) -> Optional[str]:
    """URL to navigate to for like/repost UI actions.

    Prefers the original URL as given when it's already a full LinkedIn post
    permalink (/posts/... or /feed/update/...). The numeric id embedded in a
    /posts/{slug}-{id}-{suffix}/ permalink is a share or ugcPost id, which is
    NOT always a valid urn:li:activity: id — reconstructing
    /feed/update/urn:li:activity:{that_id}/ from it can land on a "Post
    introuvable" page even though the original permalink loads fine. Only
    synthesize a feed/update URL when there's no full URL to begin with
    (a bare URN or numeric activity id).
    """
    ref = post_ref.strip()
    if _FULL_POST_URL_PATTERN.match(ref):
        return ref.split("?")[0].split("#")[0]

    activity_id = activity_id_from_post_ref(ref)
    if not activity_id:
        return None
    return f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"


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
                    raise RepostError(
                        "Non authentifié ou token expiré, lance authenticate d'abord"
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
