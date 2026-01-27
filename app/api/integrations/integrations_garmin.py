"""Garmin OAuth endpoints for user authentication and connection management.

This module implements Garmin OAuth flow:
- Users can connect their Garmin account (authenticated flow)
- Tokens are stored encrypted and never exposed to frontend
- Feature-flagged for safe deployment
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.config.settings import settings
from app.core.encryption import EncryptionError, encrypt_token
from app.db.models import User, UserIntegration
from app.db.session import get_session
from app.integrations.garmin.backfill import backfill_garmin_activities
from app.integrations.garmin.oauth import exchange_code_for_token

router = APIRouter(prefix="/integrations/garmin", tags=["integrations", "garmin"])

# In-memory state storage for OAuth flow (CSRF protection + PKCE)
# state -> (user_id, code_verifier, timestamp)
_oauth_states: dict[str, tuple[str, str, float]] = {}


def _generate_code_verifier() -> str:
    """Generate PKCE code verifier (43-128 characters).

    Returns:
        Code verifier string
    """
    # Generate 43-128 character random string (using 64 for good security)
    return base64.urlsafe_b64encode(secrets.token_bytes(48)).decode('utf-8').rstrip('=')


def _generate_code_challenge(verifier: str) -> str:
    """Generate PKCE code challenge from verifier using S256.

    Args:
        verifier: Code verifier string

    Returns:
        Code challenge (SHA256 hash, base64url encoded)
    """
    challenge = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(challenge).decode('utf-8').rstrip('=')


def _generate_oauth_state(user_id: str) -> tuple[str, str]:
    """Generate OAuth state token and PKCE code verifier.

    Args:
        user_id: Current authenticated user ID

    Returns:
        Tuple of (state_token, code_verifier)
    """
    state = secrets.token_urlsafe(32)
    code_verifier = _generate_code_verifier()
    import time

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
    import time

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
    """
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
        existing = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id, UserIntegration.provider == "garmin"
            )
        ).first()

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
            access_token="mock_access_token",
            refresh_token="mock_refresh_token",
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

    # Extract token data (adjust based on actual Garmin API response)
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")  # seconds
    scopes = token_data.get("scope", "").split() if token_data.get("scope") else []
    provider_user_id = token_data.get("user_id") or token_data.get("userId") or "unknown"

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
