"""Garmin OAuth endpoints for user authentication and connection management.

This module implements Garmin OAuth flow:
- Users can connect their Garmin account (authenticated flow)
- Tokens are stored encrypted and never exposed to frontend
- Feature-flagged for safe deployment
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import ProgrammingError

from app.api.dependencies.auth import get_current_user_id
from app.config.settings import settings
from app.core.encryption import EncryptionError, encrypt_token
from app.db.models import User, UserIntegration
from app.db.session import get_engine, get_session
from app.integrations.garmin.backfill import backfill_garmin_activities
from app.integrations.garmin.oauth import exchange_code_for_token
from app.integrations.garmin.webhook_handlers import handle_activities_webhook

router = APIRouter(prefix="/integrations/garmin", tags=["integrations", "garmin"])

# In-memory state storage for OAuth flow (CSRF protection + PKCE)
# state -> (user_id, code_verifier, timestamp)
_oauth_states: dict[str, tuple[str, str, float]] = {}

# Permissions webhook often arrives before/during callback with userId. Store for callback fallback.
# (userId, timestamp); cleared when used or after 120s.
# Note: in-memory only. Multi-worker deployments may need Redis/DB for cross-process sharing.
_last_permissions_user_id: tuple[str, float] | None = None


def _generate_code_verifier() -> str:
    """Generate PKCE code verifier (43-128 characters).

    Returns:
        Code verifier string
    """
    # Generate 43-128 character random string (using 64 for good security)
    return base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("utf-8").rstrip("=")


def _generate_code_challenge(verifier: str) -> str:
    """Generate PKCE code challenge from verifier using S256.

    Args:
        verifier: Code verifier string

    Returns:
        Code challenge (SHA256 hash, base64url encoded)
    """
    challenge = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(challenge).decode("utf-8").rstrip("=")


def _generate_oauth_state(user_id: str) -> tuple[str, str]:
    """Generate OAuth state token and PKCE code verifier.

    Args:
        user_id: Current authenticated user ID

    Returns:
        Tuple of (state_token, code_verifier)
    """
    state = secrets.token_urlsafe(32)
    code_verifier = _generate_code_verifier()

    _oauth_states[state] = (user_id, code_verifier, time.time())
    logger.debug(f"[GARMIN_OAUTH] Generated state and PKCE verifier for user_id={user_id}")
    return state, code_verifier


def _validate_and_extract_state(state: str) -> tuple[bool, str | None, str | None]:
    """Validate OAuth state and extract user_id and code_verifier.

    Args:
        state: OAuth state token

    Returns:
        Tuple of (is_valid, user_id, code_verifier)
    """
    if state not in _oauth_states:
        logger.warning(f"[GARMIN_OAUTH] Invalid state token: {state[:16]}...")
        return False, None, None

    user_id, code_verifier, timestamp = _oauth_states[state]
    # State expires after 10 minutes
    if time.time() - timestamp > 600:
        logger.warning(f"[GARMIN_OAUTH] Expired state token: {state[:16]}...")
        del _oauth_states[state]
        return False, None, None

    # Clean up used state
    del _oauth_states[state]
    logger.debug(f"[GARMIN_OAUTH] Validated state for user_id={user_id}")
    return True, user_id, code_verifier


def _try_provider_user_id_from_jwt(access_token: str | None) -> str | None:
    """Try to extract Garmin user ID from JWT access token payload.

    Garmin token response often omits user_id; the access token may be a JWT
    with sub or user_id in the payload. Decode without verification.

    Returns:
        User ID string if found, None otherwise.
    """
    if not access_token or not isinstance(access_token, str):
        return None
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            logger.debug("[GARMIN_OAUTH] JWT decode: not exactly 3 segments")
            return None
        payload_b64 = parts[1]
        pad = 4 - len(payload_b64) % 4
        if pad != 4:
            payload_b64 += "=" * pad
        raw = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(raw)
        keys = list(payload.keys()) if isinstance(payload, dict) else []
        logger.debug("[GARMIN_OAUTH] JWT payload keys: {}", keys)
        uid = (
            payload.get("sub")
            or payload.get("user_id")
            or payload.get("userId")
            or payload.get("garmin_user_id")
            or payload.get("userAccountId")
            or payload.get("accountId")
            or payload.get("garminId")
            or payload.get("uat")
        )
        if uid is None:
            return None
        s = str(uid).strip()
        if not s or s.lower() == "unknown":
            return None
        return s
    except Exception as e:
        logger.debug("[GARMIN_OAUTH] JWT decode failed: {}", e)
        return None


def _store_permissions_user_id(user_id: str) -> None:
    """Store userId from permissions webhook for callback fallback."""
    global _last_permissions_user_id
    _last_permissions_user_id = (user_id, time.time())


def _take_recent_permissions_user_id(max_age_seconds: int = 120) -> str | None:
    """Take and clear stored permissions userId if present and recent."""
    global _last_permissions_user_id
    if _last_permissions_user_id is None:
        return None
    uid, ts = _last_permissions_user_id
    if time.time() - ts > max_age_seconds:
        _last_permissions_user_id = None
        return None
    _last_permissions_user_id = None
    return uid


def _sanitize_token_response(token_data: dict) -> dict:
    """Sanitize token response for logging (remove sensitive tokens).
    
    Args:
        token_data: Raw token response dictionary
        
    Returns:
        Sanitized dictionary with tokens masked
    """
    sanitized = token_data.copy()
    # Mask sensitive tokens but keep structure
    if "access_token" in sanitized:
        token = sanitized["access_token"]
        sanitized["access_token"] = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"
    if "refresh_token" in sanitized:
        token = sanitized["refresh_token"]
        sanitized["refresh_token"] = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"
    return sanitized


def _check_table_exists(table_name: str) -> bool:
    """Check if a database table exists.

    Args:
        table_name: Name of the table to check

    Returns:
        True if table exists, False otherwise
    """
    try:
        engine = get_engine()
        inspector = inspect(engine)
        return inspector.has_table(table_name)  # pyright: ignore[reportOptionalMemberAccess]
    except Exception as e:
        logger.warning(f"[GARMIN_OAUTH] Failed to check if table {table_name} exists: {e}")
        return False


def _encrypt_and_store_tokens(
    user_id: str,
    provider_user_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: datetime | None,
    scopes: list[str],
) -> None:
    """Encrypt and store Garmin tokens in database.

    Args:
        user_id: User ID
        provider_user_id: Garmin user ID
        access_token: Access token to encrypt
        refresh_token: Refresh token to encrypt
        expires_at: Token expiration timestamp (TIMESTAMPTZ, nullable)
        scopes: OAuth scopes granted

    Raises:
        HTTPException: If table doesn't exist (migrations not run) or other errors
    """
    # Check if table exists before trying to use it
    if not _check_table_exists("user_integrations"):
        logger.error("[GARMIN_OAUTH] user_integrations table does not exist. Migrations need to be run.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database migrations not completed. Please run migrations: POST /admin/migrations/garmin?all=true",
        )

    try:
        encrypted_access_token = encrypt_token(access_token)
        encrypted_refresh_token = encrypt_token(refresh_token)
        logger.debug(f"[GARMIN_OAUTH] Tokens encrypted for user_id={user_id}")
    except EncryptionError as e:
        logger.error(f"[GARMIN_OAUTH] Token encryption failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to encrypt tokens",
        ) from e

    with get_session() as session:
        # Check if integration exists, handling schema mismatch gracefully
        existing = None
        try:
            existing = session.execute(
                select(UserIntegration).where(
                    UserIntegration.user_id == user_id, UserIntegration.provider == "garmin"
                )
            ).first()
        except ProgrammingError as e:
            # Check if this is a schema mismatch error (missing column)
            error_str = str(e.orig) if hasattr(e, "orig") else str(e)
            if "historical_backfill_cursor_date" in error_str or "does not exist" in error_str:
                logger.error(
                    f"[GARMIN_OAUTH] Database schema mismatch - missing column. "
                    f"Migration must be run before connecting Garmin. Error: {error_str}"
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Database schema mismatch: Migration required. Please contact support or run migrations.",
                ) from e
            # Re-raise if it's a different error
            raise

        if existing:
            integration = existing[0]
            logger.info(f"[GARMIN_OAUTH] Updating existing Garmin integration for user_id={user_id}")
            integration.provider_user_id = provider_user_id
            integration.access_token = encrypted_access_token
            integration.refresh_token = encrypted_refresh_token
            integration.token_expires_at = expires_at
            integration.scopes = {"scopes": scopes}
            if integration.revoked_at:
                integration.revoked_at = None  # Reconnect if previously revoked
        else:
            logger.info(f"[GARMIN_OAUTH] Creating new Garmin integration for user_id={user_id}")
            integration = UserIntegration(
                user_id=user_id,
                provider="garmin",
                provider_user_id=provider_user_id,
                access_token=encrypted_access_token,
                refresh_token=encrypted_refresh_token,
                token_expires_at=expires_at,
                scopes={"scopes": scopes},
            )
            session.add(integration)

        session.commit()
        logger.info(f"[GARMIN_OAUTH] Tokens stored successfully for user_id={user_id}")

        # Trigger backfill on integration creation (90 days)
        try:
            logger.info(f"[GARMIN_OAUTH] Triggering backfill for new integration: user_id={user_id}")
            backfill_result = backfill_garmin_activities(user_id)
            logger.info(f"[GARMIN_OAUTH] Backfill result: {backfill_result}")
        except Exception as e:
            logger.warning(f"[GARMIN_OAUTH] Backfill failed (non-critical): {e}")
            # Don't fail OAuth flow if backfill fails


def _check_garmin_preconditions(user_id: str) -> None:
    """Check Garmin connect preconditions.

    Asserts:
    - User is authenticated (already checked by get_current_user_id)
    - User has email
    - User email is verified (for OAuth providers, email is verified by provider)

    Args:
        user_id: User ID to check

    Raises:
        HTTPException: If preconditions are not met
    """
    with get_session() as session:
        user = session.execute(select(User).where(User.id == user_id)).first()
        if not user:
            logger.error(f"[GARMIN_OAUTH] User not found: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        user_obj = user[0]

        # Assert user has email
        if not user_obj.email:
            logger.warning(f"[GARMIN_OAUTH] User {user_id} has no email")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email is required to connect Garmin",
                headers={"X-Error-Code": "EMAIL_REQUIRED"},
            )

        # For OAuth providers (Google), email is verified by the provider
        # For email/password, we assume email is verified if they can log in
        # In a production system, you'd check an email_verified field
        # For now, if user has email and can authenticate, we consider them verified
        logger.debug(f"[GARMIN_OAUTH] Preconditions satisfied for user_id={user_id}, email={user_obj.email}")


@router.get("/connect")
def garmin_connect(user_id: str = Depends(get_current_user_id)):
    """Initiate Garmin OAuth flow.

    Requires authentication and verified email. Users must have credentials before linking Garmin.
    Links Garmin to the authenticated user's existing account.

    Args:
        user_id: Current authenticated user ID (required)

    Returns:
        RedirectResponse to Garmin OAuth authorization URL

    Raises:
        HTTPException: If Garmin integration is disabled, not configured, or preconditions not met
    """
    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_OAUTH] Garmin integration disabled for user_id={user_id}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Garmin integration is not enabled",
        )

    # Enforce preconditions
    _check_garmin_preconditions(user_id)

    logger.info(f"[GARMIN_OAUTH] Connect initiated for authenticated user_id={user_id}")

    # Validate Garmin credentials are configured
    if not settings.garmin_client_id or not settings.garmin_client_secret:
        logger.error("[GARMIN_OAUTH] Garmin credentials not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Garmin integration not configured",
        )

    # Validate redirect URI
    if not settings.garmin_redirect_uri or "/integrations/garmin/callback" not in settings.garmin_redirect_uri:
        logger.error(f"[GARMIN_OAUTH] Invalid redirect URI: {settings.garmin_redirect_uri}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Garmin redirect URI must point to /integrations/garmin/callback",
        )

    # Generate CSRF-protected state and PKCE verifier
    state, code_verifier = _generate_oauth_state(user_id)
    code_challenge = _generate_code_challenge(code_verifier)

    # Build Garmin OAuth URL with PKCE (Garmin requires OAuth 2.0 PKCE)
    # URL-encode all parameters to ensure exact match with registered redirect_uri
    params = {
        "response_type": "code",
        "client_id": settings.garmin_client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "redirect_uri": settings.garmin_redirect_uri,
        "state": state,
    }
    oauth_url = "https://connect.garmin.com/oauth2Confirm?" + urlencode(params)

    logger.info(f"[GARMIN_OAUTH] OAuth URL generated for user_id={user_id}")
    logger.debug(f"[GARMIN_OAUTH] OAuth URL: {oauth_url[:100]}...")

    # Redirect to Garmin OAuth
    return RedirectResponse(url=oauth_url)


@router.get("/callback")
def garmin_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle Garmin OAuth callback and store encrypted tokens.

    Validates state (CSRF protection), exchanges code for tokens,
    encrypts tokens, and stores them in user_integrations table.

    Args:
        code: Authorization code from Garmin
        state: OAuth state token for CSRF protection
        error: Error code from Garmin (if OAuth failed)

    Returns:
        RedirectResponse to frontend on success, error response on failure

    Raises:
        HTTPException: If OAuth flow fails
    """
    logger.info(f"[GARMIN_OAUTH] Callback received with state: {state[:16] if state else 'None'}...")

    # Handle OAuth errors
    if error:
        logger.error(f"[GARMIN_OAUTH] OAuth error from Garmin: {error}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Garmin OAuth error: {error}",
        )

    if not code or not state:
        logger.error("[GARMIN_OAUTH] Missing code or state in callback")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code or state",
        )

    # Validate state and get code_verifier for PKCE
    is_valid, user_id, code_verifier = _validate_and_extract_state(state)
    if not is_valid or not user_id or not code_verifier:
        logger.error("[GARMIN_OAUTH] Invalid or expired state token")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authorization request",
        )

    # Feature flag: Only exchange tokens if enabled
    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_OAUTH] Garmin integration disabled, using mock tokens for user_id={user_id}")
        # Store mock tokens for testing
        _encrypt_and_store_tokens(
            user_id=user_id,
            provider_user_id="mock_garmin_user_id",
            access_token="mock_access_token",  # noqa: S106
            refresh_token="mock_refresh_token",  # noqa: S106
            expires_at=datetime.now(timezone.utc).replace(hour=23, minute=59, second=59),
            scopes=["activity"],
        )
        return RedirectResponse(url=f"{settings.frontend_url}/settings?garmin=connected")

    # Exchange code for tokens (with PKCE code_verifier)
    try:
        token_data = exchange_code_for_token(
            client_id=settings.garmin_client_id,
            client_secret=settings.garmin_client_secret,
            code=code,
            redirect_uri=settings.garmin_redirect_uri,
            code_verifier=code_verifier,
        )
    except Exception as e:
        logger.error(f"[GARMIN_OAUTH] Token exchange failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to exchange authorization code",
        ) from e

    # Log full token response to identify user ID field (temporary debugging)
    logger.info(f"[GARMIN_OAUTH] Token response keys: {list(token_data.keys())}")
    logger.debug(f"[GARMIN_OAUTH] Full token response (sanitized): {_sanitize_token_response(token_data)}")

    # Extract token data (adjust based on actual Garmin API response)
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")  # seconds
    scopes = token_data.get("scope", "").split() if token_data.get("scope") else []
    
    # Extract Garmin User ID (UAT). Token response often omits it; fall back to JWT payload.
    provider_user_id = (
        token_data.get("user_id")
        or token_data.get("userId")
        or token_data.get("sub")
        or token_data.get("subject")
        or token_data.get("garmin_user_id")
        or token_data.get("garminUserId")
    )
    if not provider_user_id and access_token:
        provider_user_id = _try_provider_user_id_from_jwt(access_token)
        if provider_user_id:
            logger.info("[GARMIN_OAUTH] Resolved provider_user_id from JWT access token payload")

    if not provider_user_id:
        provider_user_id = _take_recent_permissions_user_id()
        if provider_user_id:
            logger.info(
                "[GARMIN_OAUTH] Resolved provider_user_id from recent permissions webhook"
            )

    if not provider_user_id:
        logger.error(
            "[GARMIN_OAUTH] No provider_user_id in token response, JWT payload, or permissions webhook. "
            "Available token keys: {}",
            list(token_data.keys()),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Garmin OAuth succeeded but user ID not found in token response. This is a critical error.",
        )
    
    # Prevent storing "unknown" as provider_user_id
    if provider_user_id == "unknown" or provider_user_id.lower() == "unknown":
        logger.error(
            f"[GARMIN_OAUTH] Invalid provider_user_id: 'unknown'. "
            f"Token response keys: {list(token_data.keys())}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Garmin OAuth succeeded but user ID is invalid. This is a critical error.",
        )
    
    logger.info(f"[GARMIN_OAUTH] Extracted Garmin provider_user_id: {provider_user_id}")

    if not access_token or not refresh_token:
        logger.error("[GARMIN_OAUTH] Missing tokens in token response")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid token response from Garmin",
        )

    # Calculate expiration time
    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_in)

    # Store tokens
    _encrypt_and_store_tokens(
        user_id=user_id,
        provider_user_id=str(provider_user_id),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=scopes,
    )

    logger.info(f"[GARMIN_OAUTH] OAuth flow completed successfully for user_id={user_id}")
    return RedirectResponse(url=f"{settings.frontend_url}/settings?garmin=connected")


@router.delete("")
def garmin_disconnect(user_id: str = Depends(get_current_user_id)):
    """Disconnect Garmin integration.

    Marks integration as revoked, stops future syncs.
    Does NOT delete historical activities.

    Args:
        user_id: Current authenticated user ID (required)

    Returns:
        Success response

    Raises:
        HTTPException: If integration not found
    """
    logger.info(f"[GARMIN_OAUTH] Disconnect initiated for user_id={user_id}")

    with get_session() as session:
        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
            )
        ).first()

        if not integration:
            logger.warning(f"[GARMIN_OAUTH] No Garmin integration found for user_id={user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Garmin integration not found",
            )

        integration_obj = integration[0]

        # Mark as revoked (soft delete)
        integration_obj.revoked_at = datetime.now(timezone.utc)
        session.commit()

        logger.info(f"[GARMIN_OAUTH] Garmin integration disconnected for user_id={user_id}")

        return {"status": "disconnected", "provider": "garmin"}


@router.post("/history-backfill")
def trigger_history_backfill(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
):
    """Trigger Garmin summary backfill (event-driven). Same as /me/sync/now for Garmin.

    Triggers GET /wellness-api/rest/backfill/activities only. Data arrives via webhooks.
    No pull. Use force=True to bypass recent-request skip.
    """
    logger.info(f"[GARMIN_HISTORY] Summary backfill triggered for user_id={user_id}")

    with get_session() as session:
        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
                UserIntegration.revoked_at.is_(None),
            )
        ).first()

        if not integration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Garmin integration not found",
            )

    def run_backfill():
        try:
            result = backfill_garmin_activities(user_id=user_id, force=True)
            logger.info(f"[GARMIN_HISTORY] Summary backfill completed for user_id={user_id}: {result}")
        except Exception as e:
            logger.exception(f"[GARMIN_HISTORY] Summary backfill failed for user_id={user_id}: {e}")

    background_tasks.add_task(run_backfill)

    return {
        "status": "scheduled",
        "message": "Summary backfill triggered. Activities will arrive via webhooks.",
        "user_id": user_id,
    }


# ----- Garmin webhook / ping endpoints (configured in Garmin Developer Portal) -----


@router.get("/activities/ping")
def garmin_activities_ping():
    """Ping endpoint for Garmin Activities subscription verification."""
    return JSONResponse(status_code=200, content={"status": "ok", "message": "pong"})


@router.get("/activity-details/ping")
def garmin_activity_details_ping():
    """Ping endpoint for Garmin Activity Details subscription verification."""
    return JSONResponse(status_code=200, content={"status": "ok", "message": "pong"})


@router.post("/activities")
async def garmin_webhook_activities(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Garmin Activities webhook callback. Same behavior as /webhooks/garmin/activities."""
    body = await request.body()
    return handle_activities_webhook(body, background_tasks)


@router.post("/activity-details")
async def garmin_webhook_activity_details(request: Request):
    """Garmin Activity Details webhook callback. ACK fast; log for now."""
    body = await request.body()
    snippet = body.decode(errors="replace")[:500]
    logger.info("[GARMIN_WEBHOOK] Activity-details callback: {}", snippet)
    return JSONResponse(status_code=200, content={"status": "acknowledged"})


@router.post("/deregister")
async def garmin_webhook_deregister(request: Request):
    """Garmin COMMON Deregistrations callback. ACK fast; log payload."""
    body = await request.body()
    snippet = body.decode(errors="replace")[:500]
    logger.info("[GARMIN_WEBHOOK] Deregister callback: {}", snippet)
    return JSONResponse(status_code=200, content={"status": "acknowledged"})


@router.post("/permissions")
async def garmin_webhook_permissions(request: Request):
    """Garmin COMMON User Permissions Change callback. ACK fast; log payload.

    Extracts userId and stores for OAuth callback fallback (token response often
    omits user ID; permissions webhook arrives before/during callback).
    """
    body = await request.body()
    snippet = body.decode(errors="replace")[:500]
    logger.info("[GARMIN_WEBHOOK] Permissions callback: {}", snippet)
    try:
        data = json.loads(body)
        changes = data.get("userPermissionsChange") or []
        if changes and isinstance(changes, list):
            first = changes[0]
            if isinstance(first, dict):
                uid = first.get("userId") or first.get("user_id")
                if uid:
                    _store_permissions_user_id(str(uid).strip())
                    logger.debug("[GARMIN_WEBHOOK] Stored permissions userId for callback fallback")
    except Exception as e:
        logger.debug("[GARMIN_WEBHOOK] Could not parse permissions userId: {}", e)
    return JSONResponse(status_code=200, content={"status": "acknowledged"})
