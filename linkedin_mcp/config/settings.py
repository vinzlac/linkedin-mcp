"""MCP LinkedIn server configuration."""
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import HttpUrl, SecretStr, Field
from pydantic_settings import BaseSettings


def _default_session_path() -> str:
    """Return a per-user private default path for Playwright session storage."""
    home = Path.home()
    if os.name == "nt":
        base_dir = Path(os.getenv("APPDATA", home / "AppData" / "Roaming"))
    elif os.uname().sysname == "Darwin":
        base_dir = home / "Library" / "Application Support"
    else:
        base_dir = Path(os.getenv("XDG_STATE_HOME", home / ".local" / "state"))
    return str(base_dir / "linkedin-mcp" / "linkedin_session.json")


class Settings(BaseSettings):
    """Application settings."""

    # LinkedIn OAuth Settings
    load_dotenv()
    LINKEDIN_CLIENT_ID: SecretStr = os.getenv("LINKEDIN_CLIENT_ID")
    LINKEDIN_CLIENT_SECRET: SecretStr = os.getenv("LINKEDIN_CLIENT_SECRET")
    LINKEDIN_REDIRECT_URI: HttpUrl = os.getenv("LINKEDIN_REDIRECT_URI")

    # API Endpoints
    LINKEDIN_AUTH_URL: HttpUrl = Field(
        default="https://www.linkedin.com/oauth/v2/authorization",
        description="LinkedIn OAuth authorization endpoint"
    )
    LINKEDIN_TOKEN_URL: HttpUrl = Field(
        default="https://www.linkedin.com/oauth/v2/accessToken",
        description="LinkedIn OAuth token endpoint"
    )
    LINKEDIN_USERINFO_URL: HttpUrl = Field(
        default="https://api.linkedin.com/v2/userinfo",
        description="LinkedIn user info endpoint"
    )
    LINKEDIN_POST_URL: HttpUrl = Field(
        default="https://api.linkedin.com/v2/ugcPosts",
        description="LinkedIn posts endpoint"
    )
    LINKEDIN_REST_POSTS_URL: HttpUrl = Field(
        default="https://api.linkedin.com/rest/posts",
        description="LinkedIn REST posts endpoint (create + repost)"
    )
    LINKEDIN_ASSET_REGISTER_URL: HttpUrl = Field(
        default="https://api.linkedin.com/v2/assets?action=registerUpload",
        description="LinkedIn asset registration endpoint"
    )

    # OAuth Scopes
    LINKEDIN_SCOPES: list[str] = [
        "openid",  # For authentication
        "profile",  # Basic profile access
        "email",  # Email address access
        "w_member_social"  # Required for posting
    ]

    # API Version Headers
    LINKEDIN_VERSION: str = "202210"  # LinkedIn legacy API version (ugcPosts)
    LINKEDIN_REST_VERSION: str = "202602"  # REST /posts API version
    RESTLI_PROTOCOL_VERSION: str = "2.0.0"  # Rest.li protocol version

    # Scraping Settings
    LINKEDIN_SESSION_PATH: str = _default_session_path()
    """Per-user path to Playwright session file (override via env if needed)."""
    LINKEDIN_HEADLESS: bool = False
    """Run Playwright headless for scrape/repost (no visible Chrome window).

    Defaults to False per linkedin_scraper ADR-002: classic headless=True is
    trivially fingerprinted by LinkedIn (navigator.webdriver, missing
    plugins, "HeadlessChrome" UA, degraded canvas/WebGL) and gets sessions
    rate-limited or checkpoint-challenged quickly. On a Linux server without
    a display, use Xvfb (ADR-011) rather than flipping this back to True.

    Ignored entirely when LINKEDIN_CDP_URL is set.
    """
    LINKEDIN_CDP_URL: str = os.getenv("LINKEDIN_CDP_URL", "")
    """Connect to an existing Chromium over CDP instead of launching a local
    one — e.g. "http://192.168.1.153:9222" for the homelab's
    chromium-cdp-host (see linkedin_scraper ADR-017). No browser window ever
    appears locally, since the browser runs on the remote host. Empty string
    (default) launches a local browser as before.
    """

    # Token Storage Settings
    TOKEN_STORAGE_PATH: str = os.path.join("linkedin_mcp", "tokens")

    # Logging Configuration
    LOG_LEVEL: str = "INFO"

    # MCP Transport Settings
    MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio")
    """"stdio" (default, Claude Desktop) or "streamable-http" (container deployment)."""
    MCP_HOST: str = os.getenv("MCP_HOST", "0.0.0.0")
    MCP_PORT: int = int(os.getenv("MCP_PORT", "8000"))

    class Config:
        """Pydantic config."""
        env_file = ".env"
        case_sensitive = True
        validate_default = True
        extra = "forbid"

    @property
    def formatted_scopes(self) -> str:
        """Get properly formatted scope string."""
        return " ".join(self.LINKEDIN_SCOPES)


# Initialize settings
settings = Settings()

# Validate required settings
if not settings.LINKEDIN_CLIENT_ID:
    raise ValueError("LINKEDIN_CLIENT_ID must be set")
if not settings.LINKEDIN_CLIENT_SECRET:
    raise ValueError("LINKEDIN_CLIENT_SECRET must be set")
if not settings.LINKEDIN_REDIRECT_URI:
    raise ValueError("LINKEDIN_REDIRECT_URI must be set")
