"""Strava OAuth endpoints for user authentication and connection management.

This module implements Step 3: Connection-only OAuth flow.
- Users can connect their Strava account
- Users can disconnect their Strava account
- Tokens are stored encrypted and never exposed to frontend
- No activity ingestion or background jobs
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from loguru import logger
from sqlalchemy import select

from app.core.auth import get_current_user
from app.core.encryption import EncryptionError, encrypt_token
from app.core.settings import settings
from app.integrations.strava.oauth import exchange_code_for_token
from app.state.db import get_session
from app.state.models import StravaAccount

router = APIRouter(prefix="/auth/strava", tags=["auth", "strava"])

# In-memory state storage for OAuth flow (CSRF protection)
# In production, consider using Redis or database-backed storage
_oauth_states: dict[str, tuple[str, float]] = {}  # state -> (user_id, timestamp)


def _generate_oauth_state(user_id: str) -> str:
    """Generate a secure OAuth state token tied to user session.

    Args:
        user_id: Current authenticated user ID

    Returns:
        Secure random state token
    """
    state = secrets.token_urlsafe(32)
    timestamp = datetime.now(timezone.utc).timestamp()
    _oauth_states[state] = (user_id, timestamp)
    logger.debug(f"Generated OAuth state for user_id={user_id}: {state[:16]}...")
    return state


def _get_user_id_from_state(state: str) -> str | None:
    """Extract user_id from OAuth state token.

    Args:
        state: OAuth state token from callback

    Returns:
        user_id if state is valid, None otherwise
    """
    if state not in _oauth_states:
        logger.warning(f"Invalid OAuth state: {state[:16]}... (not found)")
        return None

    stored_user_id, timestamp = _oauth_states[state]
    current_time = datetime.now(timezone.utc).timestamp()

    # State expires after 10 minutes
    if current_time - timestamp > 600:
        logger.warning(f"OAuth state expired: {state[:16]}...")
        del _oauth_states[state]
        return None

    # Clean up used state
    del _oauth_states[state]
    logger.debug(f"Extracted user_id={stored_user_id} from OAuth state")
    return stored_user_id


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


@router.get("")
def strava_connect(user_id: str = Depends(get_current_user)):
    """Initiate Strava OAuth flow for authenticated user.

    Requires authenticated user. Returns OAuth URL for frontend to redirect.
    Includes CSRF-protected state parameter tied to user session.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        JSON response with redirect_url, oauth_url, and url fields containing
        the Strava OAuth URL. Frontend should redirect to this URL.
    """
    logger.info(f"[STRAVA_OAUTH] Connect initiated for user_id={user_id}")

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
):
    """Handle Strava OAuth callback and store encrypted tokens.

    Validates state (CSRF protection), extracts user_id from state,
    exchanges code for tokens, encrypts tokens, and stores them in strava_accounts table.

    Args:
        code: Authorization code from Strava
        state: OAuth state token for CSRF protection (contains user_id)
        request: FastAPI request object

    Returns:
        HTMLResponse with success/error message and redirect
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

    # Extract user_id from state (CSRF protection)
    user_id = _get_user_id_from_state(state)
    if not user_id:
        logger.error(f"[STRAVA_OAUTH] Invalid or expired state: {state[:16]}...")
        return f"""
        <html>
        <head>
            <title>Strava Connection Failed</title>
            <meta http-equiv="refresh" content="5;url={redirect_url}">
        </head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #FF5722;">✗ Strava Connection Failed</h2>
            <p>Invalid or expired authorization request. Please try again.</p>
            <p><small>Redirecting in 5 seconds...</small></p>
            <p><a href="{redirect_url}">Return to app</a></p>
        </body>
        </html>
        """

    logger.info(f"[STRAVA_OAUTH] Callback validated for user_id={user_id}")
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

        # Extract token data
        athlete_id = str(token_data["athlete"]["id"])
        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        expires_at = token_data["expires_at"]

        logger.info(f"[STRAVA_OAUTH] OAuth successful for user_id={user_id}, athlete_id={athlete_id}")

        # Encrypt tokens
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

        # Store tokens in database (upsert)
        with get_session() as session:
            existing = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

            if existing:
                account = existing[0]
                logger.info(f"[STRAVA_OAUTH] Updating existing Strava account for user_id={user_id}")
                account.athlete_id = athlete_id
                account.access_token = encrypted_access_token
                account.refresh_token = encrypted_refresh_token
                account.expires_at = expires_at
                # Don't update last_sync_at on reconnect
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

        logger.info(f"[STRAVA_OAUTH] Strava connection completed for user_id={user_id}, athlete_id={athlete_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STRAVA_OAUTH] Error in OAuth callback: {e}", exc_info=True)
        return f"""
        <html>
        <head>
            <title>Strava Connection Failed</title>
            <meta http-equiv="refresh" content="5;url={redirect_url}">
        </head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #FF5722;">✗ Strava Connection Failed</h2>
            <p>Error: {e!s}</p>
            <p><small>Check backend logs for details. Redirecting in 5 seconds...</small></p>
            <p><a href="{redirect_url}">Return to app</a></p>
        </body>
        </html>
        """

    return f"""
    <html>
    <head>
        <title>Strava Connected</title>
        <meta http-equiv="refresh" content="3;url={redirect_url}">
    </head>
    <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
        <h2 style="color: #4FC3F7;">✓ Strava Connected Successfully!</h2>
        <p><small>Redirecting to Virtus AI...</small></p>
        <p><a href="{redirect_url}">Click here if not redirected</a></p>
    </body>
    </html>
    """


@router.post("/disconnect")
def strava_disconnect(user_id: str = Depends(get_current_user)):
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
