"""LinkedIn post reading via legacy /v2/shares API."""
import logging

import httpx

from ..config.settings import settings
from ..linkedin.auth import AuthError, LinkedInOAuth

logger = logging.getLogger(__name__)


def _format_post_legacy(raw: dict) -> dict:
    """Extract useful fields from a raw /v2/shares response."""
    return {
        "urn": raw.get("activity"),
        "created": raw.get("created", {}).get("time"),
        "visibility": raw.get("visibility", {}).get("code"),
        "text": raw.get("text", {}).get("text", ""),
    }


class PostReaderLegacy:
    """Reader for LinkedIn posts via legacy /v2/shares endpoint."""

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

    async def get_posts_legacy(self, count: int = 10) -> list[dict]:
        """Call GET /v2/shares (legacy API) and return formatted posts.

        Note: requires r_member_social scope (Marketing Developer Platform).
        """
        count = min(count, 50)
        person_id = self.auth_client.user_id
        if not person_id:
            raise AuthError("Non authentifié, lance authenticate d'abord")

        params = {
            "q": "owners",
            "owners": f"urn:li:person:{person_id}",
            "sharesPerOwner": count,
            "count": count,
        }
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.linkedin.com/v2/shares",
                headers=self._headers,
                params=params,
            )

        if response.status_code == 401:
            raise AuthError("Non authentifié, lance authenticate d'abord")
        if response.status_code == 403:
            raise AuthError(
                "Accès refusé : r_member_social requis même pour l'API legacy /v2/shares"
            )
        response.raise_for_status()

        data = response.json()
        return [_format_post_legacy(p) for p in data.get("elements", [])]
