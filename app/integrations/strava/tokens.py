from __future__ import annotations

import datetime as dt

import requests
from loguru import logger


def refresh_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, str | int]:
    """Exchange refresh token for new access token."""
    logger.debug("Refreshing Strava access token")
    try:
        resp = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        logger.error(f"Token refresh failed: {e.response.status_code} - {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during token refresh: {e}")
        raise
    else:
        token_data = resp.json()
        logger.info("Access token refreshed successfully")
        return token_data


def is_token_expired(expires_at: int) -> bool:
    """Check if token is expired based on expires_at timestamp."""
    expires_datetime = dt.datetime.fromtimestamp(expires_at, tz=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc) >= expires_datetime


def get_token_expiry_datetime(expires_at: int) -> dt.datetime:
    """Convert expires_at timestamp to datetime."""
    return dt.datetime.fromtimestamp(expires_at, tz=dt.timezone.utc)
