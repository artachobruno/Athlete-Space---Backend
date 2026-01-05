"""Strava OAuth endpoints for user authentication and connection management.

This module implements Step 3: Connection-only OAuth flow.
- Users can connect their Strava account
- Users can disconnect their Strava account
- Tokens are stored encrypted and never exposed to frontend
- No activity ingestion or background jobs
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.core.auth_jwt import create_access_token, decode_access_token
from app.core.encryption import EncryptionError, encrypt_token
from app.core.settings import settings
from app.ingestion.background_sync import sync_user_activities
from app.ingestion.tasks import history_backfill_task
from app.integrations.strava.oauth import exchange_code_for_token
from app.state.db import get_session
from app.state.models import StravaAccount, User

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


def _resolve_or_create_user_id(athlete_id: str, user_id: str | None) -> str:
    """Resolve or create user_id for athlete.

    Args:
        athlete_id: Strava athlete ID
        user_id: Existing user_id from state (None if unauthenticated)

    Returns:
        Resolved or created user_id
    """
    if user_id is not None:
        return user_id

    with get_session() as session:
        existing_account = session.execute(select(StravaAccount).where(StravaAccount.athlete_id == athlete_id)).first()

        if existing_account:
            resolved_user_id = existing_account[0].user_id
            logger.info(f"[STRAVA_OAUTH] Found existing account for athlete_id={athlete_id}, user_id={resolved_user_id}")
            return resolved_user_id

        resolved_user_id = f"user_{athlete_id}"
        user_result = session.execute(select(User).where(User.id == resolved_user_id)).first()
        if not user_result:
            new_user = User(id=resolved_user_id, email=None)
            session.add(new_user)
            session.commit()
            logger.info(f"[STRAVA_OAUTH] Created new user_id={resolved_user_id} for athlete_id={athlete_id}")

        return resolved_user_id


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
        expires_at: Token expiration timestamp
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

    with get_session() as session:
        existing = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if existing:
            account = existing[0]
            logger.info(f"[STRAVA_OAUTH] Updating existing Strava account for user_id={user_id}")
            account.athlete_id = athlete_id
            account.access_token = encrypted_access_token
            account.refresh_token = encrypted_refresh_token
            account.expires_at = expires_at
        else:
            logger.info(f"[STRAVA_OAUTH] Creating new Strava account for user_id={user_id}")
            account = StravaAccount(
                user_id=user_id,
                athlete_id=athlete_id,
                access_token=encrypted_access_token,
                refresh_token=encrypted_refresh_token,
                expires_at=expires_at,
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
def strava_connect(request: Request):
    """Initiate Strava OAuth flow.

    Can be called with or without authentication. If authenticated, links Strava to existing user.
    If not authenticated, creates new user after OAuth callback.

    Args:
        request: FastAPI request object

    Returns:
        JSON response with redirect_url, oauth_url, and url fields containing
        the Strava OAuth URL. Frontend should redirect to this URL.
    """
    # Try to get user_id from auth header if present (optional)
    user_id: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.replace("Bearer ", "").strip()
            user_id = decode_access_token(token)
            logger.info(f"[STRAVA_OAUTH] Connect initiated for authenticated user_id={user_id}")
        except Exception:
            # Invalid token - continue as unauthenticated
            user_id = None
            logger.info("[STRAVA_OAUTH] Connect initiated for unauthenticated user (will create user after OAuth)")
    else:
        logger.info("[STRAVA_OAUTH] Connect initiated for unauthenticated user (will create user after OAuth)")

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

    # Generate CSRF-protected state
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
    background_tasks: BackgroundTasks | None = None,
):
    """Handle Strava OAuth callback and store encrypted tokens.

    Validates state (CSRF protection), extracts user_id from state,
    exchanges code for tokens, encrypts tokens, and stores them in strava_accounts table.
    On success, redirects to frontend with JWT token in URL query parameter.

    Args:
        code: Authorization code from Strava
        state: OAuth state token for CSRF protection (contains user_id)
        request: FastAPI request object
        background_tasks: Optional FastAPI background tasks for async operations

    Returns:
        RedirectResponse to frontend with token in URL on success,
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
        return _create_error_html(redirect_url)

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

        user_id = _resolve_or_create_user_id(athlete_id, user_id)
        logger.info(f"[STRAVA_OAUTH] OAuth successful for user_id={user_id}, athlete_id={athlete_id}")

        _encrypt_and_store_tokens(user_id, athlete_id, access_token, refresh_token, expires_at)

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

    # Redirect to frontend with JWT token in URL query parameter
    redirect_with_token = f"{redirect_url}?token={jwt_token}"
    logger.info(f"[STRAVA_OAUTH] Redirecting to frontend with token for user_id={user_id}")
    return RedirectResponse(url=redirect_with_token)


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
            logger.warning(f"[STRAVA_OAUTH] No Strava account found for user_id={user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Strava account not connected",
            )

        athlete_id = account[0].athlete_id
        session.delete(account[0])
        session.commit()
        logger.info(f"[STRAVA_OAUTH] Disconnected Strava account for user_id={user_id}, athlete_id={athlete_id}")

    return {"success": True, "message": "Strava account disconnected"}


def _trigger_initial_sync(user_id: str, background_tasks: BackgroundTasks | None) -> None:
    """Trigger initial sync and history backfill for new Strava connection.

    Args:
        user_id: User ID to sync
        background_tasks: FastAPI background tasks (optional)
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

        # Also trigger history backfill to ensure we get all historical data beyond 90 days
        # Use background tasks if available, otherwise use threading
        if background_tasks is not None:
            background_tasks.add_task(history_backfill_task, user_id)
            logger.info(f"[STRAVA_OAUTH] History backfill scheduled via background_tasks for user_id={user_id}")
        else:
            # Fallback to threading if background_tasks not available
            def trigger_backfill():
                try:
                    history_backfill_task(user_id)
                except Exception as e:
                    logger.error(f"[STRAVA_OAUTH] History backfill failed for user_id={user_id}: {e}", exc_info=True)

            backfill_thread = threading.Thread(target=trigger_backfill, daemon=True)
            backfill_thread.start()
            logger.info(f"[STRAVA_OAUTH] History backfill scheduled via thread for user_id={user_id}")
    except Exception as e:
        logger.error(f"[STRAVA_OAUTH] Failed to trigger initial sync for user_id={user_id}: {e}", exc_info=True)
        # Don't fail OAuth if sync fails - user can manually trigger sync later
