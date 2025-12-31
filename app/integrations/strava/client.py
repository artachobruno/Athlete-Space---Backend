from __future__ import annotations

import datetime as dt

import httpx

from app.ingestion.quota_manager import quota_manager
from app.integrations.strava.schemas import StravaActivity

STRAVA_BASE_URL = "https://www.strava.com/api/v3"


class StravaClient:
    """Thin Strava API client.

    - No pagination
    - No sleeping
    - Global quota-aware
    """

    def __init__(self, access_token: str):
        self._access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def fetch_recent_activities(
        self,
        *,
        after: dt.datetime,
        per_page: int = 30,
    ) -> list[StravaActivity]:
        """Fetch ONE PAGE of activities after timestamp.

        Incremental-safe.
        """
        quota_manager.wait_for_slot()

        resp = httpx.get(
            f"{STRAVA_BASE_URL}/athlete/activities",
            headers=self._headers(),
            params={
                "after": int(after.timestamp()),
                "per_page": per_page,
            },
            timeout=15,
        )

        quota_manager.update_from_headers(dict(resp.headers))

        resp.raise_for_status()

        payload = resp.json()
        if not payload:
            return []

        return [StravaActivity(**raw, raw=raw) for raw in payload]

    def fetch_backfill_page(
        self,
        *,
        page: int,
        per_page: int = 30,
    ) -> list[StravaActivity]:
        """Fetch ONE historical page for backfill.

        Pagination is controlled by the caller.
        """
        quota_manager.wait_for_slot()

        resp = httpx.get(
            f"{STRAVA_BASE_URL}/athlete/activities",
            headers=self._headers(),
            params={
                "page": page,
                "per_page": per_page,
            },
            timeout=15,
        )

        quota_manager.update_from_headers(dict(resp.headers))
        resp.raise_for_status()

        payload = resp.json()
        if not payload:
            return []

        return [StravaActivity(**raw, raw=raw) for raw in payload]
