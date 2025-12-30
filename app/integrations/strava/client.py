from __future__ import annotations

import datetime as dt
import time

import httpx
from loguru import logger

from app.integrations.strava.schemas import StravaActivity

STRAVA_BASE_URL = "https://www.strava.com/api/v3"


class StravaClient:
    """Strava API client.

    Access tokens are ephemeral and passed in at construction time.
    They are never stored and are discarded after use.
    """

    def __init__(self, access_token: str):
        self._access_token = access_token
        logger.debug("StravaClient initialized")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def fetch_activities(
        self,
        *,
        since: dt.datetime,
        until: dt.datetime,
        per_page: int = 50,
    ) -> list[StravaActivity]:
        """Fetch activities from Strava API.

        Handles pagination and rate limiting automatically.
        If access token expires mid-request, this will raise an HTTPError
        which should be caught by the caller to refresh and retry.

        Args:
            since: Start datetime for activities
            until: End datetime for activities
            per_page: Number of activities per page

        Returns:
            List of StravaActivity objects

        Raises:
            httpx.HTTPStatusError: If API request fails (e.g., expired token)
        """
        logger.info(f"Fetching Strava activities: since={since.isoformat()}, until={until.isoformat()}")
        page = 1
        activities: list[StravaActivity] = []

        while True:
            logger.debug(f"Fetching page {page} of activities")
            try:
                resp = httpx.get(
                    f"{STRAVA_BASE_URL}/athlete/activities",
                    headers=self._headers(),
                    params={
                        "after": int(since.timestamp()),
                        "before": int(until.timestamp()),
                        "page": page,
                        "per_page": per_page,
                    },
                    timeout=15,
                )

                resp.raise_for_status()
                payload = resp.json()

                if not payload:
                    logger.debug(f"No more activities on page {page}, stopping pagination")
                    break

                page_activities = [StravaActivity(**raw, raw=raw) for raw in payload]
                activities.extend(page_activities)
                logger.debug(f"Fetched {len(page_activities)} activities from page {page}")

                page += 1
                time.sleep(0.2)  # Strava rate-limit safety
            except httpx.HTTPStatusError as e:
                logger.error(f"Strava API error on page {page}: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error fetching activities: {e}")
                raise

        logger.info(f"Fetched {len(activities)} total activities from Strava")
        return activities
