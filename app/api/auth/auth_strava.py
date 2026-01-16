"""Strava OAuth endpoints for user authentication and connection management.

This module implements Step 3: Connection-only OAuth flow.
- Users can connect their Strava account
- Users can disconnect their Strava account
- Tokens are stored encrypted and never exposed to frontend
- No activity ingestion or background jobs
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.config.settings import settings
from app.core.auth_jwt import create_access_token, decode_access_token
from app.core.encryption import EncryptionError, encrypt_token
from app.db.models import StravaAccount, User
from app.db.session import get_session
from app.ingestion.background_sync import sync_user_activities
from app.ingestion.tasks import history_backfill_task
from app.integrations.strava.client import StravaClient
from app.integrations.strava.oauth import exchange_code_for_token
from app.metrics.daily_aggregation import aggregate_daily_training
from app.users.profile_service import merge_strava_profile

router = APIRouter(prefix="/auth/strava", tags=["auth", "strava"])

# In-memory state storage for OAuth flow (CSRF protection)
# In production, consider using Redis or database-backed storage
# state -> (user_id | None, timestamp) - user_id is None for unauthenticated users
_oauth_states: dict[str, tuple[str | None, float]] = {}


def _generate_oauth_state(user_id: str | None = None) -> str:
    """Generate a secure OAuth state token tied to user session.

    Args:
        user_id: Current authenticated user ID (None for unauthenticated users)

    Returns:
        Secure random state token
    """
    state = secrets.token_urlsafe(32)
    timestamp = datetime.now(timezone.utc).timestamp()
    _oauth_states[state] = (user_id, timestamp)
    logger.debug(f"Generated OAuth state for user_id={user_id}: {state[:16]}...")
    return state


def _validate_and_extract_state(state: str) -> tuple[bool, str | None]:
    """Validate OAuth state and extract user_id.

    Args:
        state: OAuth state token from callback

    Returns:
        Tuple of (is_valid, user_id)
        - is_valid: True if state is valid (not expired, exists)
        - user_id: user_id if state is valid, None otherwise
    """
    if state not in _oauth_states:
        logger.warning(f"Invalid OAuth state: {state[:16]}... (not found)")
        return (False, None)

    stored_user_id, timestamp = _oauth_states[state]
    current_time = datetime.now(timezone.utc).timestamp()

    # State expires after 10 minutes
    if current_time - timestamp > 600:
        logger.warning(f"OAuth state expired: {state[:16]}...")
        del _oauth_states[state]
        return (False, None)

    # Clean up used state
    del _oauth_states[state]
    logger.debug(f"Extracted user_id={stored_user_id} from OAuth state")
    return (True, stored_user_id)


def _get_user_id_from_state(state: str) -> str | None:
    """Extract user_id from OAuth state token (deprecated - use _validate_and_extract_state).

    Args:
        state: OAuth state token from callback

    Returns:
        user_id if state is valid and user was authenticated, None if state is valid but user was not authenticated
    """
    is_valid, user_id = _validate_and_extract_state(state)
    return user_id if is_valid else None


def _validate_oauth_state(state: str, user_id: str) -> bool:
    """Validate OAuth state token and ensure it matches the user.

    Args:
        state: OAuth state token from callback
        user_id: Expected user ID

    Returns:
        True if state is valid and matches user, False otherwise
    """
    extracted_user_id = _get_user_id_from_state(state)
    if extracted_user_id is None:
        return False

    if extracted_user_id != user_id:
        logger.warning(f"OAuth state user mismatch: state={state[:16]}..., stored_user={extracted_user_id}, expected_user={user_id}")
        return False

    logger.debug(f"Validated OAuth state for user_id={user_id}")
    return True


def _resolve_or_create_user_id(athlete_id: str, user_id: str) -> str:
    """Resolve user_id for athlete.

    User must be authenticated (have credentials). This function only links Strava to existing users.

    Args:
        athlete_id: Strava athlete ID
        user_id: Authenticated user_id from state (required)

    Returns:
        Resolved user_id

    Raises:
        HTTPException: If user_id is not provided or user doesn't exist
    """
    if not user_id:
        logger.error("[STRAVA_OAUTH] Cannot link Strava: user must be authenticated")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign up with email and password first.",
        )

    athlete_id_int = int(athlete_id)

    with get_session() as session:
        # Verify user exists and has credentials
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            logger.error(f"[STRAVA_OAUTH] User not found: user_id={user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        user = user_result[0]

        # Verify user has email (required for OAuth linking)
        if not user.email:
            logger.error(f"[STRAVA_OAUTH] User missing email: user_id={user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must have an email address. Please sign up first.",
            )

        # Schema v2: Check StravaAccount table instead of User.strava_athlete_id
        existing_account = session.execute(
            select(StravaAccount).where(StravaAccount.user_id == user_id)
        ).first()

        if existing_account:
            # Verify this athlete_id matches the existing StravaAccount's athlete_id
            existing_athlete_id = int(existing_account[0].athlete_id)
            if existing_athlete_id != athlete_id_int:
                logger.warning(
                    f"[STRAVA_OAUTH] Athlete ID mismatch: user_id={user_id}, "
                    f"existing={existing_athlete_id}, new={athlete_id_int}"
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This Strava account is already linked to another user",
                )
            # Account already exists and matches - nothing to do
        # If no account exists, tokens will be stored later in _encrypt_and_store_tokens
        # which creates the StravaAccount record

        return user_id


def _encrypt_and_store_tokens(
    user_id: str,
    athlete_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: int,
) -> None:
    """Encrypt and store Strava tokens in database.

    Args:
        user_id: User ID
        athlete_id: Strava athlete ID
        access_token: Access token to encrypt
        refresh_token: Refresh token to encrypt
        expires_at: Token expiration timestamp (UNIX epoch seconds)
    """
    try:
        encrypted_access_token = encrypt_token(access_token)
        encrypted_refresh_token = encrypt_token(refresh_token)
        logger.debug(f"[STRAVA_OAUTH] Tokens encrypted for user_id={user_id}")
    except EncryptionError as e:
        logger.error(f"[STRAVA_OAUTH] Token encryption failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to encrypt tokens",
        ) from e

    # Convert expires_at from UNIX epoch seconds to datetime (schema v2: TIMESTAMPTZ)
    expires_at_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)

    # Guard: Ensure expires_at is a timezone-aware datetime
    assert isinstance(expires_at_dt, datetime)
    assert expires_at_dt.tzinfo is not None

    with get_session() as session:
        existing = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if existing:
            account = existing[0]
            logger.info(f"[STRAVA_OAUTH] Updating existing Strava account for user_id={user_id}")
            account.athlete_id = athlete_id
            account.access_token = encrypted_access_token
            account.refresh_token = encrypted_refresh_token
            account.expires_at = expires_at_dt
        else:
            logger.info(f"[STRAVA_OAUTH] Creating new Strava account for user_id={user_id}")
            account = StravaAccount(
                user_id=user_id,
                athlete_id=athlete_id,
                access_token=encrypted_access_token,
                refresh_token=encrypted_refresh_token,
                expires_at=expires_at_dt,
                last_sync_at=None,
            )
            session.add(account)

        session.commit()
        logger.info(f"[STRAVA_OAUTH] Tokens stored successfully for user_id={user_id}")


def _create_error_html(redirect_url: str, error_msg: str = "") -> str:
    """Create HTML error response for OAuth failures.

    Args:
        redirect_url: URL to redirect to
        error_msg: Optional error message to display

    Returns:
        HTML string with error page
    """
    error_content = f"<p>Error: {error_msg}</p>" if error_msg else "<p>Invalid or expired authorization request. Please try again.</p>"
    return f"""
    <html>
    <head>
        <title>Strava Connection Failed</title>
        <meta http-equiv="refresh" content="5;url={redirect_url}">
    </head>
    <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
        <h2 style="color: #FF5722;">✗ Strava Connection Failed</h2>
        {error_content}
        <p><small>Redirecting in 5 seconds...</small></p>
        <p><a href="{redirect_url}">Return to app</a></p>
    </body>
    </html>
    """


@router.get("")
def strava_connect(user_id: str = Depends(get_current_user_id)):
    """Initiate Strava OAuth flow.

    Requires authentication. Users must have credentials before linking Strava.
    Links Strava to the authenticated user's existing account.

    Args:
        user_id: Current authenticated user ID (required)

    Returns:
        JSON response with redirect_url, oauth_url, and url fields containing
        the Strava OAuth URL. Frontend should redirect to this URL.
    """
    logger.info(f"[STRAVA_OAUTH] Connect initiated for authenticated user_id={user_id}")

    # Validate Strava credentials are configured
    if not settings.strava_client_id or not settings.strava_client_secret:
        logger.error("[STRAVA_OAUTH] Strava credentials not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Strava integration not configured",
        )

    # Validate redirect URI
    if not settings.strava_redirect_uri or "/auth/strava/callback" not in settings.strava_redirect_uri:
        logger.error(f"[STRAVA_OAUTH] Invalid redirect URI: {settings.strava_redirect_uri}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Strava redirect URI must point to /auth/strava/callback",
        )

    # Generate CSRF-protected state (user_id is always present since auth is required)
    state = _generate_oauth_state(user_id)

    # Build Strava OAuth URL
    oauth_url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={settings.strava_client_id}"
        "&response_type=code"
        f"&redirect_uri={settings.strava_redirect_uri}"
        "&scope=activity:read_all"
        "&approval_prompt=auto"
        f"&state={state}"
    )

    logger.info(f"[STRAVA_OAUTH] OAuth URL generated for user_id={user_id}")
    logger.debug(f"[STRAVA_OAUTH] OAuth URL: {oauth_url[:100]}...")
    # Return JSON instead of redirect to avoid CORS issues with Location header
    # Frontend will handle the redirect
    return {"redirect_url": oauth_url, "oauth_url": oauth_url, "url": oauth_url}


@router.get("/callback", response_class=HTMLResponse)
def strava_callback(
    code: str,
    state: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Handle Strava OAuth callback and store encrypted tokens.

    Validates state (CSRF protection), extracts user_id from state,
    exchanges code for tokens, encrypts tokens, and stores them in strava_accounts table.
    On success, sets HTTP-only cookie and redirects to frontend (no token in URL).

    Args:
        code: Authorization code from Strava
        state: OAuth state token for CSRF protection (contains user_id)
        request: FastAPI request object
        background_tasks: Optional FastAPI background tasks for async operations

    Returns:
        RedirectResponse to frontend with HTTP-only cookie on success,
        HTMLResponse with error message on failure
    """
    logger.info(f"[STRAVA_OAUTH] Callback received with state: {state[:16]}...")

    # Determine frontend URL for redirect
    redirect_url = settings.frontend_url
    if redirect_url == "http://localhost:8501":
        host = request.headers.get("host", "")
        if "onrender.com" in host:
            redirect_url = "https://pace-ai.onrender.com"
        elif host and not host.startswith("localhost"):
            redirect_url = f"https://{host}"

    is_valid, user_id = _validate_and_extract_state(state)
    if not is_valid:
        logger.error(f"[STRAVA_OAUTH] Invalid or expired state: {state[:16]}...")
        return _create_error_html(redirect_url, "Invalid or expired authorization request. Please try again.")

    if not user_id:
        logger.error(f"[STRAVA_OAUTH] Callback attempted without authentication: {state[:16]}...")
        return _create_error_html(
            redirect_url,
            "Authentication required. Please sign up with email and password before connecting Strava.",
        )

    logger.info(f"[STRAVA_OAUTH] Callback validated, user_id={user_id}")
    logger.debug(f"[STRAVA_OAUTH] Callback code: {code[:10]}... (truncated)")

    try:
        # Exchange code for tokens
        logger.info(f"[STRAVA_OAUTH] Exchanging code for tokens for user_id={user_id}")
        token_data = exchange_code_for_token(
            client_id=settings.strava_client_id,
            client_secret=settings.strava_client_secret,
            code=code,
            redirect_uri=settings.strava_redirect_uri,
        )

        athlete_id = str(token_data["athlete"]["id"])
        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        expires_at = token_data["expires_at"]

        # Resolve user_id (will verify user exists and has credentials)
        user_id = _resolve_or_create_user_id(athlete_id, user_id)
        logger.info(f"[STRAVA_OAUTH] OAuth successful for user_id={user_id}, athlete_id={athlete_id}")

        _encrypt_and_store_tokens(user_id, athlete_id, access_token, refresh_token, expires_at)

        # Update last_login_at
        with get_session() as session:
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if user_result:
                user = user_result[0]
                user.last_login_at = datetime.now(timezone.utc)
                session.commit()
                logger.debug(f"[STRAVA_OAUTH] Updated last_login_at for user_id={user_id}")

        # Fetch and merge athlete profile from Strava
        try:
            logger.info(f"[STRAVA_OAUTH] Fetching athlete profile for user_id={user_id}")
            strava_client = StravaClient(access_token=access_token)
            strava_athlete = strava_client.fetch_athlete()
            logger.info(f"[STRAVA_OAUTH] Fetched athlete profile: {strava_athlete.get('firstname')} {strava_athlete.get('lastname')}")

            with get_session() as session:
                merge_strava_profile(session, user_id, strava_athlete)
            logger.info(f"[STRAVA_OAUTH] Profile merged successfully for user_id={user_id}")
        except Exception:
            # Use !r to avoid KeyError when exception message contains curly braces
            logger.exception(
                f"[STRAVA_OAUTH] Failed to fetch/merge athlete profile for user_id={user_id}"
            )
            # Don't fail OAuth if profile fetch fails - user can still use the app

        # Trigger initial sync and history backfill to fetch at least 90 days of data
        _trigger_initial_sync(user_id, background_tasks)

        # Issue JWT token for the user
        jwt_token = create_access_token(user_id)
        logger.info(f"[STRAVA_OAUTH] JWT token issued for user_id={user_id}")

        logger.info(f"[STRAVA_OAUTH] Strava connection completed for user_id={user_id}, athlete_id={athlete_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[STRAVA_OAUTH] Error in OAuth callback")
        return _create_error_html(redirect_url, str(e))

    # Set HTTP-only cookie and redirect to frontend (no token in URL)
    response = RedirectResponse(url=redirect_url)

    # Determine cookie domain
    host = request.headers.get("host", "")
    cookie_domain: str | None = None
    if "athletespace.ai" in host or "onrender.com" in host:
        cookie_domain = ".athletespace.ai"

    response.set_cookie(
        key="session",
        value=jwt_token,
        httponly=True,
        secure=True,  # HTTPS only in production
        samesite="none",  # Required for cross-origin cookie
        path="/",  # Available for all paths
        max_age=30 * 24 * 60 * 60,  # 30 days
        domain=cookie_domain,
    )
    logger.info(f"[STRAVA_OAUTH] Redirecting to frontend with HTTP-only cookie (no token in URL) for user_id={user_id}")
    return response


@router.post("/disconnect")
def strava_disconnect(user_id: str = Depends(get_current_user_id)):
    """Disconnect user's Strava account.

    Deletes the strava_accounts row for the current user.
    Does NOT revoke tokens on Strava side (optional for later).

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success response with status
    """
    logger.info(f"[STRAVA_OAUTH] Disconnect requested for user_id={user_id}")

    with get_session() as session:
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if not account:
            logger.info(f"[STRAVA_OAUTH] Strava already disconnected for user_id={user_id}")
            return {
                "connected": False,
                "message": "Strava already disconnected",
            }

        athlete_id = account[0].athlete_id
        session.delete(account[0])
        session.commit()
        logger.info(f"[STRAVA_OAUTH] Disconnected Strava account for user_id={user_id}, athlete_id={athlete_id}")

    return {
        "connected": False,
        "message": "Strava disconnected",
    }


def _trigger_initial_sync(user_id: str, background_tasks: BackgroundTasks) -> None:
    """Trigger initial sync and history backfill for new Strava connection.

    Args:
        user_id: User ID to sync
        background_tasks: FastAPI background tasks
    """
    logger.info(f"[STRAVA_OAUTH] Triggering initial sync for user_id={user_id} to fetch 90 days of data")
    try:
        # Trigger sync in background (non-blocking)
        # This will fetch the last 90 days on first sync
        sync_result = sync_user_activities(user_id)
        if "error" in sync_result:
            logger.warning(f"[STRAVA_OAUTH] Initial sync failed for user_id={user_id}: {sync_result.get('error')}")
        else:
            logger.info(f"[STRAVA_OAUTH] Initial sync completed for user_id={user_id}: {sync_result}")

        # Trigger daily aggregation to update CTL, ATL, TSB metrics
        try:
            aggregate_daily_training(user_id)
            logger.info(f"[STRAVA_OAUTH] Daily aggregation completed for user_id={user_id}")
        except Exception as e:
            logger.exception(f"[STRAVA_OAUTH] Daily aggregation failed for user_id={user_id}: {e}")
            # Don't fail if aggregation fails

        # Also trigger history backfill to ensure we get all historical data beyond 90 days
        background_tasks.add_task(history_backfill_task, user_id)
        logger.info(f"[STRAVA_OAUTH] History backfill scheduled via background_tasks for user_id={user_id}")
    except Exception as e:
        logger.exception(f"[STRAVA_OAUTH] Failed to trigger initial sync for user_id={user_id}: {e}")
        # Don't fail OAuth if sync fails - user can manually trigger sync later
