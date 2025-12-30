from __future__ import annotations

import datetime as dt
import time

import httpx

from integrations.strava.models import StravaActivity

STRAVA_BASE_URL = "https://www.strava.com/api/v3"


class StravaClient:
    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def refresh_access_token(self) -> None:
        response = httpx.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]

    def fetch_activities(
        self,
        *,
        since: dt.datetime,
        until: dt.datetime,
        per_page: int = 50,
    ) -> list[StravaActivity]:
        page = 1
        activities: list[StravaActivity] = []

        while True:
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

            if resp.status_code == 401:
                self.refresh_access_token()
                continue

            resp.raise_for_status()
            payload = resp.json()

            if not payload:
                break

            activities.extend(
                StravaActivity(
                    **raw,
                    raw=raw,
                )
                for raw in payload
            )

            page += 1
            time.sleep(0.2)  # Strava rate-limit safety

        return activities
