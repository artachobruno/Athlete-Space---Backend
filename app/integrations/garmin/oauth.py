"""Garmin OAuth token exchange utilities."""

import requests
from loguru import logger


def exchange_code_for_token(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    """Exchange Garmin authorization code for access token.

    Args:
        client_id: Garmin application client ID
        client_secret: Garmin application client secret
        code: Authorization code from Garmin callback
        redirect_uri: Redirect URI used in authorization (must match exactly)

    Returns:
        Token response dictionary containing access_token, refresh_token, etc.

    Raises:
        requests.HTTPError: If token exchange fails
    """
    logger.info("Exchanging Garmin authorization code for access token")
    try:
        # Garmin OAuth token endpoint (update with actual Garmin API endpoint)
        resp = requests.post(
            "https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0",
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
        logger.info("Successfully exchanged Garmin code for token")
    except requests.HTTPError as e:
        error_text = e.response.text if e.response else "No response text"
        logger.error(f"Garmin OAuth token exchange failed: {e.response.status_code if e.response else 'Unknown'} - {error_text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during Garmin OAuth exchange: {e}")
        raise
    else:
        return token_data


def refresh_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Refresh Garmin access token using refresh token.

    Args:
        client_id: Garmin application client ID
        client_secret: Garmin application client secret
        refresh_token: Refresh token from stored integration

    Returns:
        Token response dictionary containing new access_token, refresh_token, expires_at, etc.

    Raises:
        requests.HTTPError: If token refresh fails
    """
    logger.info("Refreshing Garmin access token")
    try:
        # Garmin OAuth refresh endpoint (update with actual Garmin API endpoint)
        resp = requests.post(
            "https://connectapi.garmin.com/oauth-service/oauth/token/refresh",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
        logger.info("Successfully refreshed Garmin token")
    except requests.HTTPError as e:
        error_text = e.response.text if e.response else "No response text"
        logger.error(f"Garmin token refresh failed: {e.response.status_code if e.response else 'Unknown'} - {error_text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during Garmin token refresh: {e}")
        raise
    else:
        return token_data
