"""MCP server for LinkedIn integration."""
import asyncio
import json
import logging
import os
import webbrowser
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context
from pydantic import FilePath

from .linkedin.auth import LinkedInOAuth, AuthError
from .linkedin.post import PostManager, PostRequest, PostCreationError, MediaRequest, PostVisibility
from .linkedin.repost import (
    RepostManager,
    RepostError,
    is_repost_api_forbidden,
)
from .linkedin.repost_ui import RepostUI, RepostUIError
from .linkedin.like_ui import LikeUI, LikeUIError, AlreadyLikedError
from .linkedin.reader import PostReader
from .linkedin.reader_legacy import PostReaderLegacy
from .callback_server import LinkedInCallbackServer
from .utils.logging import configure_logging
from .config.settings import settings
from linkedin_scraper import (
    AuthenticationError,
    BrowserManager,
    FeedScraper,
    InvitationScraper,
    MessagingScraper,
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
    host=settings.MCP_HOST,
    port=settings.MCP_PORT,
    dependencies=[
        "httpx",
        "mcp[cli]",
        "pydantic",
        "pydantic-settings",
        "python-dotenv",
        "linkedin-playwright-scraper",
    ]
)

# Initialize LinkedIn clients
auth_client = LinkedInOAuth()
post_manager = PostManager(auth_client)
repost_manager = RepostManager(auth_client)
post_reader = PostReader(auth_client)
post_reader_legacy = PostReaderLegacy(auth_client)

# Browser Playwright pour le scraping (initialisé au premier appel)
_browser_manager: BrowserManager | None = None
_browser_initialized: bool = False

# Bounded ceiling for a single like/repost UI action. Every Playwright wait
# inside LikeUI/RepostUI already has its own timeout, but this hard cap makes
# sure a call can never appear to hang indefinitely from the MCP client's
# point of view (e.g. an unexpected LinkedIn UI state or an added navigation
# step upstream stacking waits past what the client itself is willing to wait).
_UI_ACTION_TIMEOUT_S = 100


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


_PLAYWRIGHT_INSTALL_HINT = (
    "Navigateur Playwright introuvable (souvent après nettoyage du cache macOS). "
    "Dans le dossier linkedin-mcp : `uv run playwright install chromium`, "
    "puis redémarre Claude Desktop."
)


def _playwright_start_error(exc: Exception) -> RuntimeError:
    msg = str(exc).lower()
    if "chrome-headless-shell" in msg or "executable doesn't exist" in msg:
        return RuntimeError(_PLAYWRIGHT_INSTALL_HINT)
    return RuntimeError(f"Impossible de démarrer le navigateur Playwright : {exc}")


def _browser_singleton_is_alive() -> bool:
    """True si le navigateur/page mis en cache répond encore.

    Le process Playwright peut mourir sans que ce serveur en soit informé
    (crash, OOM, redémarrage du pod CDP distant...) — sans ce contrôle,
    _browser_initialized reste bloqué à True indéfiniment et chaque appel
    réutilise un navigateur mort (Page.goto: Target page, context or
    browser has been closed), en échouant systématiquement.
    """
    if _browser_manager is None:
        return False
    try:
        return (
            _browser_manager.browser.is_connected()
            and not _browser_manager.page.is_closed()
        )
    except RuntimeError:
        # browser/page pas encore démarré (ne devrait pas arriver ici, mais
        # traité comme "mort" par prudence)
        return False


async def _get_browser() -> BrowserManager:
    """Retourne l'instance BrowserManager, en l'initialisant si nécessaire.

    Relance automatiquement un navigateur si celui en cache est mort — pas
    besoin de create_scrape_session (pas de ré-authentification, le fichier
    de session sur disque reste valide, seul le process navigateur repart).
    """
    global _browser_manager, _browser_initialized

    if _browser_initialized and _browser_singleton_is_alive():
        return _browser_manager

    if _browser_initialized:
        logger.warning(
            "Navigateur Playwright mis en cache mort/déconnecté — relance automatique"
        )
        await _close_browser_singleton()

    session_path = settings.LINKEDIN_SESSION_PATH
    if not session_path or not Path(session_path).exists():
        raise RuntimeError(
            f"Fichier de session LinkedIn introuvable : {session_path}. "
            "Crée-le avec l'outil create_scrape_session ou `uv run python create_session.py`."
        )

    cdp_url = settings.LINKEDIN_CDP_URL or None
    _browser_manager = BrowserManager(headless=settings.LINKEDIN_HEADLESS, cdp_url=cdp_url)
    try:
        await _browser_manager.start()
        await _browser_manager.load_session(session_path)
    except Exception as exc:
        _browser_manager = None
        raise _playwright_start_error(exc) from exc
    _browser_initialized = True
    logger.info(f"Navigateur Playwright initialisé avec la session {session_path}")
    return _browser_manager


async def _try_load_oauth_from_disk() -> bool:
    """Charge le token OAuth depuis le disque si la session MCP n'est pas authentifiée."""
    if auth_client.is_authenticated:
        return True
    token_dir = settings.TOKEN_STORAGE_PATH
    if not os.path.isdir(token_dir):
        return False
    for filename in os.listdir(token_dir):
        if not filename.endswith(".json"):
            continue
        user_id = filename[:-5]
        if not auth_client.load_tokens(user_id):
            continue
        try:
            await auth_client.get_user_info()
            logger.info("Token OAuth chargé depuis %s", filename)
            return True
        except AuthError:
            logger.warning("Token OAuth expiré ou invalide : %s", filename)
            return False
    return False


async def _repost_via_playwright(post_url: str, commentary: str) -> str:
    browser = await _get_browser()
    try:
        return await asyncio.wait_for(
            RepostUI(browser.page).repost(post_url, commentary=commentary),
            timeout=_UI_ACTION_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise RepostUIError(
            f"Timeout ({_UI_ACTION_TIMEOUT_S}s) en repostant — "
            "abandon plutôt que de rester bloqué. Réessaie."
        ) from exc


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
async def repost_post(
    post_url: str,
    commentary: str = "",
    visibility: PostVisibility = PostVisibility.PUBLIC,
    ctx: Context = None,
) -> str:
    """Reposte un post LinkedIn (API REST, fallback Playwright si 403).

    Essaie d'abord POST /rest/posts (OAuth). Si LinkedIn refuse (403 sur posts tiers),
    reposte via l'UI web et la session Playwright (create_scrape_session).

    Args:
        post_url: URL du post (feed/update/urn:li:activity:...) ou URN activity
        commentary: Commentaire optionnel ajouté au repost
        visibility: PUBLIC ou CONNECTIONS (API uniquement ; ignoré en fallback UI)

    Returns:
        Message de succès
    """
    logger.info("Repost LinkedIn post_url=%s", post_url)
    try:
        if ctx:
            await ctx.info(f"Repost (visibilité API={visibility})…")

        if await _try_load_oauth_from_disk():
            try:
                if not auth_client.user_id:
                    await auth_client.get_user_info()
                repost_id = await repost_manager.repost(
                    post_url,
                    commentary=commentary,
                    visibility=visibility,
                )
                success_msg = f"Repost créé via API. ID : {repost_id}"
                logger.info(success_msg)
                return success_msg
            except RepostError as api_err:
                if not is_repost_api_forbidden(api_err):
                    raise
                logger.info("Repost API refusée, fallback Playwright : %s", api_err)
                if ctx:
                    await ctx.info(
                        "API refusée pour ce post (403), repost via Playwright…"
                    )
        else:
            logger.info("Pas de token OAuth valide, repost Playwright direct")
            if ctx:
                await ctx.info(
                    "Pas de token OAuth — repost via session Playwright…"
                )

        ui_msg = await _repost_via_playwright(post_url, commentary)
        logger.info(ui_msg)
        return ui_msg

    except (AuthError, RepostError, RepostUIError) as e:
        error_msg = str(e)
        logger.error(error_msg)
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        error_msg = f"Erreur inattendue repost : {e}"
        logger.exception("Erreur repost_post")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def repost_post_scrape(
    post_url: str,
    commentary: str = "",
    ctx: Context = None,
) -> str:
    """Reposte un post LinkedIn via l'UI web (session Playwright).

    N'utilise pas l'API OAuth. Nécessite create_scrape_session.

    Args:
        post_url: URL du post ou urn:li:activity:ID
        commentary: Commentaire optionnel (vide = repost instantané)

    Returns:
        Message de succès
    """
    logger.info("Repost Playwright post_url=%s", post_url)
    try:
        if ctx:
            await ctx.info("Repost via Playwright…")
        ui_msg = await _repost_via_playwright(post_url, commentary)
        logger.info(ui_msg)
        return ui_msg
    except RepostUIError as e:
        error_msg = str(e)
        logger.error(error_msg)
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        error_msg = f"Erreur repost Playwright : {e}"
        logger.exception("Erreur repost_post_scrape")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def like_post(
    post_url: str,
    ctx: Context = None,
) -> str:
    """Like un post LinkedIn via la session Playwright (J'aime).

    Fonctionne avec les URLs activity et les cartes feed compkey.
    Nécessite create_scrape_session au préalable.

    Args:
        post_url: URL du post, urn:li:activity:ID, ou urn:li:compkey:…

    Returns:
        Message de succès ou indication que le post est déjà liké.
    """
    logger.info("Like LinkedIn post_url=%s", post_url)
    try:
        if ctx:
            await ctx.info("Like via Playwright…")
        browser = await _get_browser()
        msg = await asyncio.wait_for(
            LikeUI(browser.page).like(post_url), timeout=_UI_ACTION_TIMEOUT_S
        )
        logger.info(msg)
        return msg
    except AlreadyLikedError as e:
        return str(e)
    except LikeUIError as e:
        error_msg = str(e)
        logger.error(error_msg)
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)
    except asyncio.TimeoutError:
        error_msg = (
            f"Timeout ({_UI_ACTION_TIMEOUT_S}s) en likant le post — "
            "abandon plutôt que de rester bloqué. Réessaie."
        )
        logger.error(error_msg)
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        error_msg = f"Erreur inattendue like_post : {e}"
        logger.exception("Erreur like_post")
        if ctx:
            await ctx.error(error_msg)
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

    **Cas particulier — toujours un navigateur Playwright visible (headless=False) :**
    ouvre « Google Chrome for Testing » pour que tu te connectes manuellement sur
    linkedin.com (mot de passe, 2FA, captcha). Une fois la session sauvegardée,
    scrape_feed / scrape_post / repost_post réutilisent LINKEDIN_HEADLESS
    (False par défaut, voir ADR-002) ou se connectent au Chromium distant si
    LINKEDIN_CDP_URL est défini (voir ADR-017).

    **Indépendant de authenticate (OAuth) :**
    - authenticate → navigateur système + token API (create_post, repost API)
    - create_scrape_session → login web Playwright (scraping, repost UI)
    L'un ne remplace pas l'autre.

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
        try:
            await browser.start()
        except Exception as exc:
            raise _playwright_start_error(exc) from exc
        await browser.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        await wait_for_manual_login(browser.page, timeout=timeout_ms)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await browser.save_session(str(out_path))
        os.chmod(out_path, 0o600)
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
async def get_scrape_session_json(ctx: Context = None) -> str:
    """Retourne le contenu JSON brut de la session Playwright (scrape_feed).

    Utile pour exporter la session vers une autre machine.
    """
    session_path = Path(settings.LINKEDIN_SESSION_PATH).expanduser().resolve()
    if not session_path.exists():
        msg = (
            f"Fichier de session introuvable: {session_path}. "
            "Crée-le avec create_scrape_session."
        )
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)

    try:
        raw = session_path.read_text(encoding="utf-8")
        # Validation JSON pour éviter de renvoyer un fichier corrompu.
        json.loads(raw)
        if ctx:
            await ctx.info(f"Session Playwright lue depuis {session_path}")
        return raw
    except json.JSONDecodeError as e:
        msg = f"Session JSON invalide ({session_path}) : {e}"
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)
    except Exception as e:
        msg = f"Impossible de lire la session ({session_path}) : {e}"
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)


@mcp.tool()
async def set_scrape_session_json(session_json: str, ctx: Context = None) -> str:
    """Écrit la session Playwright depuis une chaîne JSON (ex. export d'une autre machine).

    Le navigateur de scraping actif est fermé pour garantir le rechargement de la
    nouvelle session au prochain scrape_feed.
    """
    session_path = Path(settings.LINKEDIN_SESSION_PATH).expanduser().resolve()

    try:
        payload = json.loads(session_json)
    except json.JSONDecodeError as e:
        msg = f"session_json invalide (JSON non parseable): {e}"
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)

    if not isinstance(payload, dict):
        msg = "session_json invalide: objet JSON attendu."
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)

    if "cookies" not in payload and "origins" not in payload:
        msg = (
            "session_json invalide: format Playwright storage_state attendu "
            "(clé 'cookies' et/ou 'origins')."
        )
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)

    try:
        await _close_browser_singleton()
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(session_path, 0o600)
        if ctx:
            await ctx.info(f"Session Playwright mise à jour: {session_path}")
        return (
            f"Session Playwright enregistrée : {session_path}\n"
            "Le navigateur de scraping a été fermé ; relance scrape_feed."
        )
    except Exception as e:
        msg = f"Impossible d'écrire la session ({session_path}) : {e}"
        if ctx:
            await ctx.error(msg)
        raise RuntimeError(msg)


@mcp.tool()
async def scrape_post(post_url: str, ctx: Context = None) -> str:
    """Lit un post LinkedIn précis depuis son URL.

    Accepte les URLs /feed/update/urn:li:activity:... ou /posts/{slug}-share-{id}-...
    Utilise la session Playwright (scraping web), pas l'API OAuth.
    Nécessite create_scrape_session au préalable.

    Args:
        post_url: URL complète du post LinkedIn

    Returns:
        JSON du post : auteur, texte, date, réactions, commentaires, images, URL.
    """
    logger.info("Scraping post LinkedIn : %s", post_url)
    try:
        if ctx:
            await ctx.info(f"Scraping du post : {post_url}")

        browser = await _get_browser()
        scraper = FeedScraper(browser.page)
        posts = await scraper.scrape_post_by_url(post_url)

        if not posts:
            return "Aucun post trouvé pour cette URL."

        if ctx:
            await ctx.info("Post récupéré.")

        return json.dumps(
            [p.to_public_dict() for p in posts],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    except Exception as e:
        error_msg = f"Erreur lors du scraping du post : {str(e)}"
        logger.exception("Erreur scrape_post")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


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
            [p.to_public_dict() for p in posts],
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


@mcp.tool()
async def list_pending_invitations(limit: int = 20, ctx: Context = None) -> str:
    """Liste les invitations de relation en attente (reçues).

    Utilise la session Playwright (scraping web), pas l'API OAuth.
    Nécessite create_scrape_session au préalable (ou une session déjà présente).

    Args:
        limit: Nombre max d'invitations à retourner (défaut 20)

    Returns:
        JSON des invitations : invitation_id (slug profil), nom, headline,
        message d'intro, relations en commun, URL profil.
    """
    logger.info("Listing pending invitations (limit=%s)", limit)
    try:
        if ctx:
            await ctx.info(f"Récupération des invitations (limit={limit})...")

        browser = await _get_browser()
        scraper = InvitationScraper(browser.page)
        invitations = await scraper.list_pending(limit=limit)

        if not invitations:
            return "Aucune invitation en attente."

        if ctx:
            await ctx.info(f"{len(invitations)} invitation(s) récupérée(s).")

        return json.dumps(
            [inv.to_public_dict() for inv in invitations],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except Exception as e:
        error_msg = f"Erreur lors de la liste des invitations : {str(e)}"
        logger.exception("Erreur list_pending_invitations")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def accept_invitation(invitation_id: str, ctx: Context = None) -> str:
    """Accepte une invitation LinkedIn en attente.

    Args:
        invitation_id: Identifiant actionnable (slug profil/company, ex. ``slonjon``)

    Returns:
        JSON ``{"ok": true/false, "invitation_id": "..."}``
    """
    logger.info("Accepting invitation %s", invitation_id)
    try:
        if ctx:
            await ctx.info(f"Acceptation de l'invitation {invitation_id}...")

        browser = await _get_browser()
        scraper = InvitationScraper(browser.page)
        ok = await scraper.accept(invitation_id)

        if ctx:
            await ctx.info("Invitation acceptée." if ok else "Invitation non trouvée / échec.")

        return json.dumps(
            {"ok": ok, "invitation_id": invitation_id},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        error_msg = f"Erreur accept_invitation : {str(e)}"
        logger.exception("Erreur accept_invitation")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def ignore_invitation(invitation_id: str, ctx: Context = None) -> str:
    """Ignore / refuse une invitation LinkedIn en attente.

    Args:
        invitation_id: Identifiant actionnable (slug profil/company, ex. ``slonjon``)

    Returns:
        JSON ``{"ok": true/false, "invitation_id": "..."}``
    """
    logger.info("Ignoring invitation %s", invitation_id)
    try:
        if ctx:
            await ctx.info(f"Ignore de l'invitation {invitation_id}...")

        browser = await _get_browser()
        scraper = InvitationScraper(browser.page)
        ok = await scraper.ignore(invitation_id)

        if ctx:
            await ctx.info("Invitation ignorée." if ok else "Invitation non trouvée / échec.")

        return json.dumps(
            {"ok": ok, "invitation_id": invitation_id},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        error_msg = f"Erreur ignore_invitation : {str(e)}"
        logger.exception("Erreur ignore_invitation")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def list_recent_conversations(limit: int = 20, ctx: Context = None) -> str:
    """Liste les conversations récentes de la messagerie LinkedIn.

    Utilise la session Playwright (scraping web). Nécessite create_scrape_session
    au préalable (ou une session déjà présente).

    Args:
        limit: Nombre max de conversations (défaut 20)

    Returns:
        JSON des conversations : conversation_id, participant, preview, unread…
    """
    logger.info("Listing recent conversations (limit=%s)", limit)
    try:
        if ctx:
            await ctx.info(f"Récupération des conversations (limit={limit})...")

        browser = await _get_browser()
        scraper = MessagingScraper(browser.page)
        conversations = await scraper.list_recent(limit=limit)

        if not conversations:
            return "Aucune conversation trouvée."

        if ctx:
            await ctx.info(f"{len(conversations)} conversation(s) récupérée(s).")

        return json.dumps(
            [c.to_public_dict() for c in conversations],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except Exception as e:
        error_msg = f"Erreur list_recent_conversations : {str(e)}"
        logger.exception("Erreur list_recent_conversations")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def get_conversation(
    conversation_id: str, limit: int = 50, ctx: Context = None
) -> str:
    """Récupère les messages d'une conversation LinkedIn.

    Args:
        conversation_id: Id du thread (segment d'URL ``/messaging/thread/{id}/``)
        limit: Nombre max de messages récents (défaut 50)

    Returns:
        JSON des messages : text, direction, sender, sent_at, message_id…
    """
    logger.info("Getting conversation %s (limit=%s)", conversation_id, limit)
    try:
        if ctx:
            await ctx.info(f"Lecture de la conversation {conversation_id}...")

        browser = await _get_browser()
        scraper = MessagingScraper(browser.page)
        messages = await scraper.get_conversation(conversation_id, limit=limit)

        if not messages:
            return "Aucun message trouvé dans cette conversation."

        if ctx:
            await ctx.info(f"{len(messages)} message(s) récupéré(s).")

        return json.dumps(
            [m.to_public_dict() for m in messages],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except Exception as e:
        error_msg = f"Erreur get_conversation : {str(e)}"
        logger.exception("Erreur get_conversation")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


@mcp.tool()
async def send_message(
    conversation_id: str, text: str, ctx: Context = None
) -> str:
    """Envoie un message texte dans une conversation LinkedIn existante.

    Utilise la session Playwright. Ouvre le thread, saisit le texte, puis
    envoie (bouton Envoyer/Send si présent, sinon Entrée).

    Args:
        conversation_id: Id du thread (``/messaging/thread/{id}/``)
        text: Corps du message (non vide)

    Returns:
        JSON ``{"ok": true/false, "conversation_id": "..."}``
    """
    logger.info(
        "Sending message to %s (%s chars)", conversation_id, len(text or "")
    )
    try:
        if ctx:
            await ctx.info(f"Envoi d'un message dans {conversation_id}...")

        browser = await _get_browser()
        scraper = MessagingScraper(browser.page)
        ok = await scraper.send_message(conversation_id, text)

        if ctx:
            await ctx.info("Message envoyé." if ok else "Échec d'envoi / non confirmé.")

        return json.dumps(
            {"ok": ok, "conversation_id": conversation_id},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        error_msg = f"Erreur send_message : {str(e)}"
        logger.exception("Erreur send_message")
        if ctx:
            await ctx.error(error_msg)
        raise RuntimeError(error_msg)


def main():
    """Main function for running the LinkedIn server."""
    load_dotenv()
    logger.info("Starting LinkedIn server (transport=%s)...", settings.MCP_TRANSPORT)
    if settings.MCP_TRANSPORT == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
