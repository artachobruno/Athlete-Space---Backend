"""Google OAuth endpoints for user authentication and connection management.

This module implements Google OAuth flow:
- Users can sign up/login with Google (unauthenticated flow)
- Users can connect their Google account to existing account (authenticated flow)
- Tokens are stored encrypted and never exposed to frontend
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id, get_optional_user_id
from app.config.settings import settings
from app.core.auth_jwt import create_access_token
from app.core.encryption import EncryptionError, encrypt_token
from app.db.models import AuthProvider, GoogleAccount, User
from app.db.session import get_session
from app.integrations.google.oauth import exchange_code_for_token, get_user_info

router = APIRouter(prefix="/auth/google", tags=["auth", "google"])

# In-memory state storage for OAuth flow (CSRF protection)
# In production, consider using Redis or database-backed storage
# state -> (user_id | None, platform, timestamp) - user_id is None for unauthenticated users
# platform: "web" | "mobile" - determines callback behavior
_oauth_states: dict[str, tuple[str | None, str, float]] = {}


def _generate_oauth_state(user_id: str | None = None, platform: str = "web") -> str:
    """Generate a secure OAuth state token tied to user session.

    Args:
        user_id: Current authenticated user ID (None for unauthenticated users)
        platform: Platform identifier ("web" or "mobile")

    Returns:
        Secure random state token
    """
    state = secrets.token_urlsafe(32)
    timestamp = datetime.now(timezone.utc).timestamp()
    _oauth_states[state] = (user_id, platform, timestamp)
    logger.debug(f"Generated Google OAuth state for user_id={user_id}, platform={platform}: {state[:16]}...")
    return state


def _validate_and_extract_state(state: str) -> tuple[bool, str | None, str]:
    """Validate OAuth state and extract user_id and platform.

    Args:
        state: OAuth state token from callback

    Returns:
        Tuple of (is_valid, user_id, platform)
        - is_valid: True if state is valid (not expired, exists)
        - user_id: user_id if state is valid, None otherwise
        - platform: "web" or "mobile"
    """
    if state not in _oauth_states:
        logger.warning(f"Invalid Google OAuth state: {state[:16]}... (not found)")
        return (False, None, "web")

    stored_user_id, platform, timestamp = _oauth_states[state]
    current_time = datetime.now(timezone.utc).timestamp()

    # State expires after 10 minutes
    if current_time - timestamp > 600:
        logger.warning(f"Google OAuth state expired: {state[:16]}...")
        del _oauth_states[state]
        return (False, None, "web")

    # Clean up used state
    del _oauth_states[state]
    logger.debug(f"Extracted user_id={stored_user_id}, platform={platform} from Google OAuth state")
    return (True, stored_user_id, platform)


def _encrypt_and_store_tokens(
    user_id: str,
    google_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: int,
) -> None:
    """Encrypt and store Google tokens in database.

    Args:
        user_id: User ID
        google_id: Google user ID
        access_token: Access token to encrypt
        refresh_token: Refresh token to encrypt
        expires_at: Token expiration timestamp
    """
    try:
        encrypted_access_token = encrypt_token(access_token)
        encrypted_refresh_token = encrypt_token(refresh_token)
        logger.debug(f"[GOOGLE_OAUTH] Tokens encrypted for user_id={user_id}")
    except EncryptionError as e:
        logger.error(f"[GOOGLE_OAUTH] Token encryption failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to encrypt tokens",
        ) from e

    with get_session() as session:
        existing = session.execute(select(GoogleAccount).where(GoogleAccount.user_id == user_id)).first()

        if existing:
            account = existing[0]
            logger.info(f"[GOOGLE_OAUTH] Updating existing Google account for user_id={user_id}")
            account.google_id = google_id
            account.access_token = encrypted_access_token
            account.refresh_token = encrypted_refresh_token
            account.expires_at = expires_at
        else:
            logger.info(f"[GOOGLE_OAUTH] Creating new Google account for user_id={user_id}")
            account = GoogleAccount(
                user_id=user_id,
                google_id=google_id,
                access_token=encrypted_access_token,
                refresh_token=encrypted_refresh_token,
                expires_at=expires_at,
            )
            session.add(account)

        session.commit()
        logger.info(f"[GOOGLE_OAUTH] Tokens stored successfully for user_id={user_id}")


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
        <title>Google Connection Failed</title>
        <meta http-equiv="refresh" content="5;url={redirect_url}">
    </head>
    <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
        <h2 style="color: #FF5722;">✗ Google Connection Failed</h2>
        {error_content}
        <p><small>Redirecting in 5 seconds...</small></p>
        <p><a href="{redirect_url}">Return to app</a></p>
    </body>
    </html>
    """


@router.get("/login")
def google_login(
    platform: str = "web",
    user_id: str | None = Depends(get_optional_user_id),
):
    """Initiate Google OAuth login flow (React-compatible).

    This endpoint is called by React frontend (web + mobile) and redirects directly to Google.
    Supports platform parameter to determine callback behavior:
    - platform=web: Sets cookie on callback
    - platform=mobile: Returns token via deep link

    Args:
        platform: Platform identifier ("web" or "mobile")
        user_id: Current authenticated user ID (optional)

    Returns:
        RedirectResponse to Google OAuth consent screen
    """
    # Normalize platform
    platform = platform.lower()
    if platform not in {"web", "mobile"}:
        platform = "web"
        logger.warning(f"[GOOGLE_OAUTH] Invalid platform '{platform}', defaulting to 'web'")

    logger.info(f"[GOOGLE_OAUTH] Login initiated for user_id={user_id or 'unauthenticated'}, platform={platform}")

    # Validate Google credentials are configured
    if not settings.google_client_id or not settings.google_client_secret:
        logger.error("[GOOGLE_OAUTH] Google credentials not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google integration not configured",
        )

    # Validate redirect URI
    if not settings.google_redirect_uri or "/auth/google/callback" not in settings.google_redirect_uri:
        logger.error(f"[GOOGLE_OAUTH] Invalid redirect URI: {settings.google_redirect_uri}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google redirect URI must point to /auth/google/callback",
        )

    # Generate CSRF-protected state with platform
    state = _generate_oauth_state(user_id, platform)

    # Build Google OAuth URL
    oauth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.google_client_id}"
        "&response_type=code"
        f"&redirect_uri={settings.google_redirect_uri}"
        "&scope=openid email profile"
        f"&state={state}"
        "&access_type=offline"  # Required to get refresh token
        "&prompt=consent"  # Force consent screen to get refresh token
    )

    logger.info(f"[GOOGLE_OAUTH] Redirecting to Google OAuth for user_id={user_id or 'unauthenticated'}, platform={platform}")
    logger.debug(f"[GOOGLE_OAUTH] OAuth URL: {oauth_url[:100]}...")

    # Redirect directly to Google (React expects this)
    return RedirectResponse(url=oauth_url)


@router.get("")
def google_connect(user_id: str | None = Depends(get_optional_user_id)):
    """Initiate Google OAuth flow (legacy endpoint for account linking).

    Supports both authenticated and unauthenticated flows:
    - If authenticated: Links Google to existing account
    - If not authenticated: Will create account or login on callback

    Args:
        user_id: Current authenticated user ID (optional)

    Returns:
        JSON response with redirect_url, oauth_url, and url fields containing
        the Google OAuth URL. Frontend should redirect to this URL.
    """
    logger.info(f"[GOOGLE_OAUTH] Connect initiated for user_id={user_id or 'unauthenticated'}")

    # Validate Google credentials are configured
    if not settings.google_client_id or not settings.google_client_secret:
        logger.error("[GOOGLE_OAUTH] Google credentials not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google integration not configured",
        )

    # Validate redirect URI
    if not settings.google_redirect_uri or "/auth/google/callback" not in settings.google_redirect_uri:
        logger.error(f"[GOOGLE_OAUTH] Invalid redirect URI: {settings.google_redirect_uri}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google redirect URI must point to /auth/google/callback",
        )

    # Generate CSRF-protected state (default to web for legacy endpoint)
    state = _generate_oauth_state(user_id, "web")

    # Build Google OAuth URL
    oauth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.google_client_id}"
        "&response_type=code"
        f"&redirect_uri={settings.google_redirect_uri}"
        "&scope=openid email profile"
        f"&state={state}"
        "&access_type=offline"  # Required to get refresh token
        "&prompt=consent"  # Force consent screen to get refresh token
    )

    logger.info(f"[GOOGLE_OAUTH] OAuth URL generated for user_id={user_id or 'unauthenticated'}")
    logger.debug(f"[GOOGLE_OAUTH] OAuth URL: {oauth_url[:100]}...")
    # Return JSON instead of redirect to avoid CORS issues with Location header
    # Frontend will handle the redirect
    return {"redirect_url": oauth_url, "oauth_url": oauth_url, "url": oauth_url}


@router.get("/callback", response_class=HTMLResponse)
def google_callback(
    code: str,
    state: str,
    request: Request,
):
    """Handle Google OAuth callback and store encrypted tokens.

    Validates state (CSRF protection), extracts user_id from state,
    exchanges code for tokens, gets user info, creates/updates user account,
    encrypts tokens, and stores them in google_accounts table.
    On success, sets HTTP-only cookie and redirects to frontend (no token in URL).

    Args:
        code: Authorization code from Google
        state: OAuth state token for CSRF protection (contains user_id)
        request: FastAPI request object

    Returns:
        RedirectResponse to frontend with HTTP-only cookie on success,
        HTMLResponse with error message on failure
    """
    logger.info(f"[GOOGLE_OAUTH] Callback received with state: {state[:16]}...")

    # Determine frontend URL for redirect
    # CRITICAL: Always redirect to app root ("/") not "/login"
    # AuthLanding will handle routing based on auth state
    redirect_url = settings.frontend_url
    if redirect_url == "http://localhost:8501":
        host = request.headers.get("host", "")
        if "onrender.com" in host:
            redirect_url = "https://pace-ai.onrender.com"
        elif host and not host.startswith("localhost"):
            redirect_url = f"https://{host}"

    # Ensure redirect goes to app root, not /login
    # AuthLanding will check /me and route appropriately
    if not redirect_url.endswith("/"):
        redirect_url = f"{redirect_url}/"

    is_valid, user_id, platform = _validate_and_extract_state(state)
    if not is_valid:
        logger.error(f"[GOOGLE_OAUTH] Invalid or expired state: {state[:16]}...")
        return _create_error_html(redirect_url, "Invalid or expired authorization request. Please try again.")

    logger.info(f"[GOOGLE_OAUTH] Callback validated, user_id={user_id or 'unauthenticated'}, platform={platform}")

    logger.debug(f"[GOOGLE_OAUTH] Callback code: {code[:10]}... (truncated)")

    resolved_user_id: str | None = None
    try:
        # Exchange code for tokens
        logger.info("[GOOGLE_OAUTH] Exchanging code for tokens")
        token_data = exchange_code_for_token(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            code=code,
            redirect_uri=settings.google_redirect_uri,
        )

        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)
        expires_at = int(datetime.now(timezone.utc).timestamp()) + expires_in
        # Note: id_token is available in token_data but not verified yet
        # Full ID token verification (signature, aud, exp, iss) would require
        # fetching Google's public keys. For now, we verify email_verified from userinfo.
        # TODO: Add full ID token verification using google-auth library for enhanced security

        # Get user info from Google
        logger.info("[GOOGLE_OAUTH] Fetching user info from Google")
        user_info = get_user_info(access_token)
        google_sub = user_info["id"]  # Google's 'id' is the same as 'sub' claim
        email = user_info.get("email", "").lower().strip()
        email_verified = user_info.get("verified_email", False)  # Google returns verified_email

        if not email:
            logger.error("[GOOGLE_OAUTH] No email in Google user info")
            return _create_error_html(redirect_url, "Google account must have an email address.")

        if not email_verified:
            logger.error("[GOOGLE_OAUTH] Email not verified in Google user info")
            return _create_error_html(redirect_url, "Google account email must be verified.")

        # Resolve or create user
        with get_session() as session:
            # First, check if user exists by google_sub
            user_by_google_sub = session.execute(select(User).where(User.google_sub == google_sub)).first()

            if user_by_google_sub:
                # User exists with this google_sub - login
                resolved_user_id = user_by_google_sub[0].id
                logger.info(f"[GOOGLE_OAUTH] Logging in existing user_id={resolved_user_id} by google_sub={google_sub}")
            else:
                # Check if user exists by email (for account linking)
                user_by_email = session.execute(select(User).where(User.email == email)).first()

                if user_by_email:
                    # User exists with this email - link Google account
                    existing_user = user_by_email[0]
                    if user_id and existing_user.id != user_id:
                        logger.warning(
                            f"[GOOGLE_OAUTH] Email already exists for different user: existing={existing_user.id}, requested={user_id}"
                        )
                        return _create_error_html(redirect_url, "This email is already associated with another account.")
                    resolved_user_id = existing_user.id
                    # Link Google account to existing user
                    existing_user.google_sub = google_sub
                    existing_user.auth_provider = AuthProvider.google
                    # If user had password auth, keep it (allow both)
                    # But if they're logging in with Google, we'll update auth_provider
                    logger.info(f"[GOOGLE_OAUTH] Linking Google account to existing user_id={resolved_user_id}")
                elif user_id:
                    # Authenticated flow: link to existing user (shouldn't happen if email doesn't match)
                    user_result = session.execute(select(User).where(User.id == user_id)).first()
                    if not user_result:
                        logger.error(f"[GOOGLE_OAUTH] User not found: user_id={user_id}")
                        return _create_error_html(redirect_url, "User not found.")
                    resolved_user_id = user_id
                    user = user_result[0]
                    user.google_sub = google_sub
                    user.auth_provider = AuthProvider.google
                    logger.info(f"[GOOGLE_OAUTH] Linking Google account to existing user_id={resolved_user_id}")
                else:
                    # Unauthenticated flow: create new user
                    resolved_user_id = str(uuid.uuid4())
                    new_user = User(
                        id=resolved_user_id,
                        email=email,
                        password_hash=None,  # OAuth users don't need passwords
                        auth_provider=AuthProvider.google,
                        google_sub=google_sub,
                        strava_athlete_id=None,
                        created_at=datetime.now(timezone.utc),
                        last_login_at=None,
                    )
                    session.add(new_user)
                    session.commit()
                    logger.info(f"[GOOGLE_OAUTH] Created new user_id={resolved_user_id} with email={email}, auth_provider=google")

            # Ensure resolved_user_id is set before proceeding
            if not resolved_user_id:
                logger.error("[GOOGLE_OAUTH] resolved_user_id not set after processing")
                return _create_error_html(redirect_url, "Failed to resolve user account.")

            # Store tokens in GoogleAccount table (for future use if needed)
            _encrypt_and_store_tokens(resolved_user_id, google_sub, access_token, refresh_token, expires_at)

            # Update last_login_at
            user_result = session.execute(select(User).where(User.id == resolved_user_id)).first()
            if user_result:
                user = user_result[0]
                user.last_login_at = datetime.now(timezone.utc)
                session.commit()
                logger.debug(f"[GOOGLE_OAUTH] Updated last_login_at for user_id={resolved_user_id}")

        logger.info(f"[GOOGLE_OAUTH] Google connection completed for user_id={resolved_user_id}, google_sub={google_sub}")

        # Issue JWT token for the user
        jwt_token = create_access_token(resolved_user_id)
        logger.info(f"[GOOGLE_OAUTH] JWT token issued for user_id={resolved_user_id}")

        # Determine cookie domain
        host = request.headers.get("host", "")
        cookie_domain: str | None = None
        if "onrender.com" in host:
            cookie_domain = ".virtus-ai.onrender.com"

        # CRITICAL: Always set HTTP-only cookie for both web and mobile
        # Mobile WebView can persist cookies if they're set correctly with secure=True and samesite="none"
        response = RedirectResponse(url=redirect_url)

        response.set_cookie(
            key="session",
            value=jwt_token,
            httponly=True,
            secure=True,  # HTTPS only - REQUIRED for mobile cookie persistence
            samesite="none",  # REQUIRED for cross-origin cookie (mobile WebView)
            path="/",  # Available for all paths
            max_age=30 * 24 * 60 * 60,  # 30 days
            domain=cookie_domain,
        )
        logger.info(f"[GOOGLE_OAUTH] Set HTTP-only cookie for user_id={resolved_user_id}, platform={platform}")

        # Branch by platform: web redirects to frontend, mobile also redirects to frontend (cookie is set)
        # Mobile app will intercept the redirect and navigate appropriately
        # The cookie will be available for subsequent API calls
        if platform == "mobile":
            # For mobile, we still redirect to web URL first to ensure cookie is set
            # The mobile app can intercept this URL and handle navigation
            # If deep link is needed, frontend can handle it after cookie is confirmed
            logger.info(f"[GOOGLE_OAUTH] Redirecting mobile user to frontend URL (cookie set) for user_id={resolved_user_id}")
        else:
            logger.info(f"[GOOGLE_OAUTH] Redirecting web user to frontend URL (cookie set) for user_id={resolved_user_id}")

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[GOOGLE_OAUTH] Error in OAuth callback")
        return _create_error_html(redirect_url, str(e))


@router.post("/disconnect")
def google_disconnect(user_id: str = Depends(get_current_user_id)):
    """Disconnect user's Google account.

    Deletes the google_accounts row for the current user.
    Does NOT revoke tokens on Google side (optional for later).

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success response with status
    """
    logger.info(f"[GOOGLE_OAUTH] Disconnect requested for user_id={user_id}")

    with get_session() as session:
        account = session.execute(select(GoogleAccount).where(GoogleAccount.user_id == user_id)).first()

        if not account:
            logger.warning(f"[GOOGLE_OAUTH] No Google account found for user_id={user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Google account not connected",
            )

        google_id = account[0].google_id
        session.delete(account[0])
        session.commit()
        logger.info(f"[GOOGLE_OAUTH] Disconnected Google account for user_id={user_id}, google_id={google_id}")

    return {"success": True, "message": "Google account disconnected"}
