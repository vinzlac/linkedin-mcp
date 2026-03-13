"""LinkedIn post reading implementation."""
import logging

import httpx

from ..config.settings import settings
from ..linkedin.auth import AuthError, LinkedInOAuth

logger = logging.getLogger(__name__)


def _format_post(raw: dict) -> dict:
    """Extract useful fields from a raw ugcPost."""
    content = raw.get("specificContent", {}).get("com.linkedin.ugc.ShareContent", {})
    return {
        "urn": raw.get("id"),
        "created": raw.get("created", {}).get("time"),
        "visibility": raw.get("visibility", {}).get(
            "com.linkedin.ugc.MemberNetworkVisibility"
        ),
        "text": content.get("shareCommentary", {}).get("text", ""),
    }


class PostReader:
    """Reader for LinkedIn posts."""

    def __init__(self, auth_client: LinkedInOAuth) -> None:
        self.auth_client = auth_client

    @property
    def _headers(self) -> dict:
        if not self.auth_client.access_token:
            raise AuthError("Non authentifié, lance authenticate d'abord")
        return {
            "Authorization": f"Bearer {self.auth_client.access_token}",
            "X-Restli-Protocol-Version": settings.RESTLI_PROTOCOL_VERSION,
            "LinkedIn-Version": settings.LINKEDIN_VERSION,
            "Content-Type": "application/json",
        }

    async def get_posts(self, count: int = 10) -> list[dict]:
        """Call GET /v2/ugcPosts and return formatted posts."""
        count = min(count, 50)
        person_id = self.auth_client.user_id
        if not person_id:
            raise AuthError("Non authentifié, lance authenticate d'abord")

        params = {
            "q": "authors",
            "authors": f"List(urn:li:person:{person_id})",
            "count": count,
            "sortBy": "LAST_MODIFIED",
        }
        async with httpx.AsyncClient() as client:
            response = await client.get(
                str(settings.LINKEDIN_POST_URL),
                headers=self._headers,
                params=params,
            )

        if response.status_code == 401:
            raise AuthError("Non authentifié, lance authenticate d'abord")
        if response.status_code == 403:
            raise AuthError(
                "Accès refusé : le scope r_member_social n'est pas activé "
                "sur l'app LinkedIn Developer"
            )
        response.raise_for_status()

        data = response.json()
        return [_format_post(p) for p in data.get("elements", [])]
