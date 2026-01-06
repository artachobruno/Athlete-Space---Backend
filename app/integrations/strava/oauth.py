import requests
from loguru import logger


def exchange_code_for_token(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    """Exchange Strava authorization code for access token.

    Args:
        client_id: Strava application client ID
        client_secret: Strava application client secret
        code: Authorization code from Strava callback
        redirect_uri: Redirect URI used in authorization (must match exactly)

    Returns:
        Token response dictionary containing access_token, refresh_token, etc.

    Raises:
        requests.HTTPError: If token exchange fails
    """
    logger.info("Exchanging authorization code for access token")
    try:
        resp = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,  # Required by Strava, must match authorization
            },
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
        logger.info("Successfully exchanged code for token")
    except requests.HTTPError as e:
        error_text = e.response.text if e.response else "No response text"
        logger.error(f"OAuth token exchange failed: {e.response.status_code if e.response else 'Unknown'} - {error_text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during OAuth exchange: {e}")
        raise
    else:
        return token_data
