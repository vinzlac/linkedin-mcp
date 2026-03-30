"""MCP server for LinkedIn integration."""
import json
import logging
import webbrowser
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context
from pydantic import FilePath

from .linkedin.auth import LinkedInOAuth, AuthError
from .linkedin.post import PostManager, PostRequest, PostCreationError, MediaRequest, PostVisibility
from .linkedin.reader import PostReader
from .linkedin.reader_legacy import PostReaderLegacy
from .callback_server import LinkedInCallbackServer
from .utils.logging import configure_logging
from .config.settings import settings
from linkedin_scraper import (
    AuthenticationError,
    BrowserManager,
    FeedScraper,
    wait_for_manual_login,
)

# Configure logging
configure_logging(
    log_level=settings.LOG_LEVEL,
)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP(
    "LinkedInServer",
    dependencies=[
        "httpx",
        "mcp[cli]",
        "pydantic",
        "pydantic-settings",
        "python-dotenv",
        "linkedin_scraper",
    ]
)

# Initialize LinkedIn clients
auth_client = LinkedInOAuth()
post_manager = PostManager(auth_client)
post_reader = PostReader(auth_client)
post_reader_legacy = PostReaderLegacy(auth_client)

# Browser Playwright pour le scraping (initialisé au premier appel)
_browser_manager: BrowserManager | None = None
_browser_initialized: bool = False


async def _close_browser_singleton() -> None:
    """Ferme le navigateur Playwright réutilisé par scrape_feed (ex. après nouvelle session)."""
    global _browser_manager, _browser_initialized
    if _browser_manager is not None:
        try:
            await _browser_manager.close()
        except Exception:
            logger.exception("Erreur à la fermeture du navigateur MCP")
        _browser_manager = None
    _browser_initialized = False


async def _get_browser() -> BrowserManager:
    """Retourne l'instance BrowserManager, en l'initialisant si nécessaire."""
    global _browser_manager, _browser_initialized

    if _browser_initialized and _browser_manager is not None:
        return _browser_manager

    session_path = settings.LINKEDIN_SESSION_PATH
    if not session_path or not Path(session_path).exists():
        raise RuntimeError(
            f"Fichier de session LinkedIn introuvable : {session_path}. "
            "Crée-le avec l'outil create_scrape_session ou `uv run python create_session.py`."
        )

    _browser_manager = BrowserManager(headless=False)
    await _browser_manager.start()
    await _browser_manager.load_session(session_path)
    _browser_initialized = True
    logger.info(f"Navigateur Playwright initialisé avec la session {session_path}")
    return _browser_manager


@mcp.tool()
async def authenticate(ctx: Context = None) -> str:
    """Start LinkedIn authentication flow and handle callback automatically.

    Returns:
        Success message after authentication
    """
    logger.info("Starting LinkedIn authentication flow...")
    callback_server = None

    try:
        # Start callback server
        callback_server = LinkedInCallbackServer(port=3000)
        await callback_server.start()

        # Get auth URL
        logger.debug("Getting authorization URL from LinkedIn")
        auth_url, expected_state = await auth_client.get_authorization_url()
        logger.debug(f"Authorization URL generated with state: {expected_state}")

        if ctx:
            ctx.info("Opening browser for authentication...")

        # Open browser
        logger.info(f"Opening browser to: {auth_url}")
        if not webbrowser.open(auth_url):
            error_msg = "Failed to open browser. Please visit the URL manually: " + auth_url
            logger.error(error_msg)
            if ctx:
                ctx.error(error_msg)
            raise RuntimeError(error_msg)

        logger.info("Waiting for authentication callback...")
        if ctx:
            ctx.info("Waiting for authentication callback...")

        # Add debug info for event status
        logger.debug(f"Auth received event status before wait: {callback_server.auth_received.is_set()}")

        try:
            import asyncio
            logger.debug("Current event loop: %s", asyncio.get_running_loop())
        except RuntimeError as e:
            logger.warning(f"Error getting event loop: {str(e)}")

        # Wait for callback with detailed error handling
        logger.debug("Calling wait_for_callback with 120 second timeout")
        code, state = await callback_server.wait_for_callback(timeout=120)  # Reduced timeout for better user experience

        logger.debug(f"Auth received event status after wait: {callback_server.auth_received.is_set()}")
        logger.debug(f"Callback result received: code={code is not None}, state={state is not None}")

        # Check code and state, providing detailed log messages
        if not code:
            logger.error("No authorization code received from callback")
            raise AuthError("Authentication failed - no authorization code received")

        if not state:
            logger.error("No state parameter received from callback")
            raise AuthError("Authentication failed - no state parameter received")

        if state != expected_state:
            logger.error(f"State mismatch. Expected: {expected_state}, Got: {state}")
            raise AuthError(f"Invalid state parameter: expected {expected_state}, got {state}")

        logger.debug(f"State parameter matches expected value: {state}")

        if ctx:
            ctx.info("Exchanging authorization code for tokens...")

        # Exchange code for tokens
        logger.info("Exchanging authorization code for tokens")
        tokens = await auth_client.exchange_code(code)
        if not tokens:
            logger.error("Failed to exchange code for tokens")
            raise AuthError("Failed to exchange authorization code for tokens")

        logger.debug("Successfully obtained tokens from authorization code")

        if ctx:
            ctx.info("Getting user info...")

        # Get and save user info
        logger.info("Getting user info & saving tokens...")
        user_info = await auth_client.get_user_info()
        logger.debug(f"User info retrieved: {user_info.sub}")

        auth_client.save_tokens(user_info.sub)
        logger.info("Tokens saved successfully")

        success_msg = f"Successfully authenticated with LinkedIn as {user_info.name}!"
        logger.info(success_msg)
        return success_msg

    except AuthError as e:
        error_msg = f"Authentication error: {str(e)}"
        logger.error(error_msg)
        if ctx:
            ctx.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        error_msg = f"Authentication failed: {str(e)}"
        logger.exception("Unexpected error during authentication")
        if ctx:
            ctx.error(error_msg)
        raise RuntimeError(error_msg)
    finally:
        # Ensure server is stopped
        if callback_server:
            logger.debug("Stopping callback server in finally block")
            callback_server.stop()


@mcp.tool()
async def create_post(
        text: str,
        media_files: List[FilePath] = None,
        media_titles: List[str] = None,
        media_descriptions: List[str] = None,
        visibility: PostVisibility = "PUBLIC",
        ctx: Context = None
) -> str:
    """Create a new post on LinkedIn.

    Args:
        text: The content of your post
        media_files: List of paths to media files to attach (images or videos)
        media_titles: Optional titles for media attachments
        media_descriptions: Optional descriptions for media attachments
        visibility: Post visibility (PUBLIC or CONNECTIONS)
        ctx: MCP Context for progress reporting

    Returns:
        Success message with post ID
    """
    logger.info("Creating LinkedIn post...")
    try:
        if ctx:
            ctx.info(f"Creating LinkedIn post with visibility: {visibility}")

        if not auth_client.is_authenticated:
            error_msg = "Not authenticated. Please authenticate first."
            logger.error(error_msg)
            if ctx:
                ctx.error(error_msg)
            raise RuntimeError(error_msg)

        # Prepare media requests if files are provided
        media_requests = None
        if media_files:
            media_requests = []
            for i, file_path in enumerate(media_files):
                title = media_titles[i] if media_titles and i < len(media_titles) else None
                description = media_descriptions[i] if media_descriptions and i < len(media_descriptions) else None

                logger.debug(f"Processing media file: {file_path}, title: {title}")
                if ctx:
                    ctx.info(f"Processing media file: {file_path}, title: {title}")

                media_requests.append(MediaRequest(
                    file_path=file_path,
                    title=title,
                    description=description
                ))

        # Create post request
        post_request = PostRequest(
            text=text,
            visibility=visibility,
            media=media_requests
        )

        # Create the post
        logger.info("Sending post to LinkedIn API")
        post_id = await post_manager.create_post(post_request)
        success_msg = f"Successfully created LinkedIn post with ID: {post_id}"
        logger.info(success_msg)

        return success_msg

    except (AuthError, PostCreationError) as e:
        error_msg = str(e)
        logger.error(error_msg)
        if ctx:
            ctx.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.exception("Unexpected error during post creation")
        if ctx:
            ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def get_posts_legacy(count: int = 10, ctx: Context = None) -> str:
    """Récupère les posts LinkedIn via l'API legacy /v2/shares.

    Alternative à get_posts quand r_member_social n'est pas disponible.
    Nécessite uniquement w_member_social.

    Args:
        count: Nombre de posts à récupérer (défaut 10, max 50)

    Returns:
        Liste des posts formatés (date, texte, visibilité, URN)
    """
    try:
        if not auth_client.is_authenticated:
            raise RuntimeError(
                "Non authentifié. Lance d'abord l'outil authenticate."
            )
        if ctx:
            await ctx.info(f"Récupération de {count} posts (API legacy)...")
        posts = await post_reader_legacy.get_posts_legacy(count)
        if not posts:
            return "Aucun post trouvé."
        return json.dumps(posts, ensure_ascii=False, indent=2)
    except AuthError as e:
        msg = str(e)
        logger.error(msg)
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)
    except RuntimeError:
        raise
    except Exception as e:
        logger.exception("Erreur inattendue dans get_posts_legacy")
        raise RuntimeError(str(e))


@mcp.tool()
async def get_posts(count: int = 10, ctx: Context = None) -> str:
    """Récupère les posts LinkedIn récents de l'utilisateur authentifié.

    Args:
        count: Nombre de posts à récupérer (défaut 10, max 50)

    Returns:
        Liste des posts formatés (date, texte, visibilité, URN)
    """
    try:
        if not auth_client.is_authenticated:
            raise RuntimeError(
                "Non authentifié. Lance d'abord l'outil authenticate."
            )
        if ctx:
            await ctx.info(f"Récupération de {count} posts...")
        posts = await post_reader.get_posts(count)
        if not posts:
            return "Aucun post trouvé."
        return json.dumps(posts, ensure_ascii=False, indent=2)
    except AuthError as e:
        msg = str(e)
        logger.error(msg)
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)
    except RuntimeError:
        raise
    except Exception as e:
        logger.exception("Erreur inattendue dans get_posts")
        raise RuntimeError(str(e))


@mcp.tool()
async def create_scrape_session(
    timeout_seconds: int = 300,
    ctx: Context = None,
) -> str:
    """Crée le fichier de session Playwright pour scrape_feed (connexion web LinkedIn).

    Ouvre Chromium (Playwright), charge la page de login, attend que tu te connectes
    manuellement (mot de passe, 2FA, captcha) jusqu'à ce que le feed soit utilisable,
    puis enregistre les cookies dans LINKEDIN_SESSION_PATH (ex. linkedin_session.json).

    Indépendant de l'outil authenticate (OAuth / API) : deux flux de connexion distincts.

    Args:
        timeout_seconds: Délai max pour terminer le login manuel (défaut 300 s).

    Returns:
        Message de confirmation avec le chemin du fichier de session.
    """
    await _close_browser_singleton()

    out_path = Path(settings.LINKEDIN_SESSION_PATH).expanduser().resolve()
    timeout_ms = max(60_000, min(timeout_seconds * 1000, 3_600_000))

    logger.info("Création session Playwright pour scrape_feed → %s", out_path)
    if ctx:
        await ctx.info(
            "Ouverture de Chromium : connecte-toi sur LinkedIn jusqu'au feed, "
            f"puis attends la sauvegarde (max {timeout_seconds // 60} min)…"
        )

    browser = BrowserManager(headless=False)
    try:
        await browser.start()
        await browser.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await wait_for_manual_login(browser.page, timeout=timeout_ms)
        await browser.save_session(str(out_path))
    except AuthenticationError as e:
        msg = f"Échec de la connexion manuelle : {e}"
        logger.error(msg)
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)
    finally:
        await browser.close()

    logger.info("Session Playwright enregistrée : %s", out_path)
    if ctx:
        await ctx.info(f"Session enregistrée : {out_path}")

    return (
        f"Session Playwright enregistrée pour le scraping du feed : {out_path}\n"
        "Tu peux maintenant utiliser l'outil scrape_feed."
    )


@mcp.tool()
async def close_scrape_browser(ctx: Context = None) -> str:
    """Ferme la fenêtre Chromium (Playwright) utilisée pour scrape_feed.

    Après un appel à scrape_feed ou create_scrape_session, le navigateur reste
    volontairement ouvert (réutilisation rapide). Tant que le processus MCP
    tourne, Cmd+Q sur « Google Chrome for Testing » peut sembler sans effet ou
    laisser des processus liés : utilise cet outil pour une fermeture propre,
    ou quitte Claude Desktop pour tout arrêter.

    Returns:
        Message indiquant si le navigateur a été fermé ou était déjà inactif.
    """
    had_browser = _browser_manager is not None
    await _close_browser_singleton()
    if ctx:
        await ctx.info("Navigateur de scraping fermé." if had_browser else "Aucun navigateur de scraping actif.")
    if had_browser:
        return "Navigateur Playwright (Chrome for Testing) fermé. Il sera relancé au prochain scrape_feed."
    return "Aucun navigateur de scraping n'était ouvert."


@mcp.tool()
async def scrape_feed(count: int = 10, ctx: Context = None) -> str:
    """Lit les N premiers posts du feed LinkedIn de l'utilisateur connecté.

    Utilise un navigateur Playwright authentifié (scraping) car l'API officielle
    LinkedIn bloque la lecture du feed pour les applications standard.
    Le navigateur reste ouvert après l'appel pour les prochains scrapes ;
    utilise close_scrape_browser pour le fermer explicitement.

    Args:
        count: Nombre de posts à récupérer (défaut 10)

    Returns:
        Liste JSON des posts avec : url, auteur, texte, date, réactions,
        commentaires, images, vidéo, lien externe.
    """
    logger.info(f"Scraping {count} posts du feed LinkedIn...")
    try:
        if ctx:
            await ctx.info(f"Démarrage du scraping du feed ({count} posts)...")

        browser = await _get_browser()
        scraper = FeedScraper(browser.page)
        posts = await scraper.scrape(limit=count)

        if not posts:
            return "Aucun post trouvé dans le feed."

        if ctx:
            await ctx.info(f"{len(posts)} posts récupérés.")

        return json.dumps(
            [p.model_dump() for p in posts],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    except Exception as e:
        error_msg = f"Erreur lors du scraping du feed : {str(e)}"
        logger.exception("Erreur scrape_feed")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


def main():
    """Main function for running the LinkedIn server."""
    load_dotenv()
    logger.info("Starting LinkedIn server...")
    mcp.run(transport="stdio")
