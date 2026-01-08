"""Google OAuth token exchange utilities."""

import requests
from loguru import logger


def exchange_code_for_token(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    """Exchange Google authorization code for access token.

    Args:
        client_id: Google application client ID
        client_secret: Google application client secret
        code: Authorization code from Google callback
        redirect_uri: Redirect URI used in authorization (must match exactly)

    Returns:
        Token response dictionary containing access_token, refresh_token, expires_in, id_token, etc.

    Raises:
        requests.HTTPError: If token exchange fails
    """
    logger.info("Exchanging Google authorization code for access token")
    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
        logger.info("Successfully exchanged Google code for token")
        # Log if id_token is present (for future verification)
        if "id_token" in token_data:
            logger.debug("Google ID token received (can be verified for additional security)")
        else:
            logger.warning("Google ID token not present in token response")
    except requests.HTTPError as e:
        error_text = e.response.text if e.response else "No response text"
        logger.error(f"Google OAuth token exchange failed: {e.response.status_code if e.response else 'Unknown'} - {error_text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during Google OAuth exchange: {e}")
        raise
    else:
        return token_data


def get_user_info(access_token: str) -> dict:
    """Get user information from Google using access token.

    Args:
        access_token: Google access token

    Returns:
        User information dictionary containing id, email, name, etc.

    Raises:
        requests.HTTPError: If API call fails
    """
    logger.info("Fetching user info from Google")
    try:
        resp = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        user_info = resp.json()
        logger.info(f"Successfully fetched Google user info for: {user_info.get('email', 'unknown')}")
    except requests.HTTPError as e:
        error_text = e.response.text if e.response else "No response text"
        logger.error(f"Google user info fetch failed: {e.response.status_code if e.response else 'Unknown'} - {error_text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during Google user info fetch: {e}")
        raise
    else:
        return user_info
