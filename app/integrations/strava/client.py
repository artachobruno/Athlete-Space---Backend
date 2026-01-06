from __future__ import annotations

import datetime as dt

import httpx
from loguru import logger

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
        logger.info(f"[STRAVA_CLIENT] Fetching recent activities after {after.isoformat()} (per_page={per_page})")
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
            logger.info("[STRAVA_CLIENT] No activities returned from API")
            return []

        activities = [StravaActivity(**raw, raw=raw) for raw in payload]
        logger.info(f"[STRAVA_CLIENT] Fetched {len(activities)} activities from Strava API")
        return activities

    def fetch_backfill_page(
        self,
        *,
        page: int,
        per_page: int = 30,
    ) -> list[StravaActivity]:
        """Fetch ONE historical page for backfill.

        Pagination is controlled by the caller.
        """
        logger.info(f"[STRAVA_CLIENT] Fetching backfill page {page} (per_page={per_page})")
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
            logger.info(f"[STRAVA_CLIENT] No activities returned from backfill page {page}")
            return []

        activities = [StravaActivity(**raw, raw=raw) for raw in payload]
        logger.info(f"[STRAVA_CLIENT] Fetched {len(activities)} activities from backfill page {page}")
        return activities

    def get_activities(
        self,
        *,
        after_ts: dt.datetime | None = None,
        before: int | None = None,
        per_page: int = 200,
    ) -> list[StravaActivity]:
        """Fetch activities from Strava API.

        Behavior depends on parameters:
        - If `before` is provided: Fetch ONE PAGE only (no pagination) - for history backfill
        - If `after_ts` is provided: Fetch all pages with automatic pagination - for incremental sync

        Args:
            after_ts: Only fetch activities after this timestamp (optional, triggers pagination)
            before: Unix timestamp - only fetch activities before this time (optional, single page only)
            per_page: Number of activities per page (max 200)

        Returns:
            List of StravaActivity objects
        """
        # If `before` is provided, fetch only one page (for history backfill)
        if before is not None:
            logger.info(f"[STRAVA_CLIENT] Fetching activities before={before} (per_page={per_page})")
            quota_manager.wait_for_slot()

            params: dict[str, int | str] = {
                "per_page": min(per_page, 200),  # Strava max is 200
                "before": before,
            }

            resp = httpx.get(
                f"{STRAVA_BASE_URL}/athlete/activities",
                headers=self._headers(),
                params=params,
                timeout=15,
            )

            quota_manager.update_from_headers(dict(resp.headers))
            resp.raise_for_status()

            payload = resp.json()
            if not payload:
                logger.info("[STRAVA_CLIENT] No activities returned from API")
                return []

            activities = [StravaActivity(**raw, raw=raw) for raw in payload]
            logger.info(f"[STRAVA_CLIENT] Fetched {len(activities)} activities from Strava API")
            return activities

        # If `after_ts` is provided, fetch all pages with pagination (for incremental sync)
        logger.info(f"[STRAVA_CLIENT] Fetching activities (after_ts={after_ts}, per_page={per_page})")
        all_activities = []
        page = 1

        while True:
            logger.debug(f"[STRAVA_CLIENT] Fetching page {page}")
            quota_manager.wait_for_slot()

            params: dict[str, int | str] = {
                "page": page,
                "per_page": min(per_page, 200),  # Strava max is 200
            }
            if after_ts:
                params["after"] = int(after_ts.timestamp())

            resp = httpx.get(
                f"{STRAVA_BASE_URL}/athlete/activities",
                headers=self._headers(),
                params=params,
                timeout=15,
            )

            quota_manager.update_from_headers(dict(resp.headers))
            resp.raise_for_status()

            payload = resp.json()
            if not payload:
                logger.info(f"[STRAVA_CLIENT] No more activities (page {page} was empty)")
                break

            page_activities = [StravaActivity(**raw, raw=raw) for raw in payload]
            all_activities.extend(page_activities)
            logger.info(f"[STRAVA_CLIENT] Fetched {len(page_activities)} activities from page {page} (total: {len(all_activities)})")

            # If we got fewer than per_page, we've reached the end
            if len(page_activities) < per_page:
                logger.info(f"[STRAVA_CLIENT] Reached end of activities (got {len(page_activities)} < {per_page})")
                break

            page += 1

        logger.info(f"[STRAVA_CLIENT] Fetched {len(all_activities)} total activities")
        return all_activities

    def fetch_activity_streams(
        self,
        *,
        activity_id: int,
        stream_types: list[str] | None = None,
    ) -> dict[str, list] | None:
        """Fetch time-series streams data for an activity.

        Args:
            activity_id: Strava activity ID
            stream_types: List of stream types to fetch. If None, fetches all available.
                         Common types: time, latlng, distance, altitude, heartrate,
                         cadence, watts, temp, velocity_smooth, grade_smooth

        Returns:
            Dictionary mapping stream type to list of values, or None if streams unavailable.
            Example: {
                "time": [0, 1, 2, ...],
                "latlng": [[lat1, lng1], [lat2, lng2], ...],
                "heartrate": [120, 125, 130, ...],
                ...
            }

        Note:
            - Streams may not be available for all activities
            - Returns None if activity not found or streams unavailable
            - Each stream type has the same length (one value per data point)
        """
        if stream_types is None:
            # Default: fetch all commonly used streams
            stream_types = [
                "time",
                "latlng",
                "distance",
                "altitude",
                "heartrate",
                "cadence",
                "watts",
                "temp",
                "velocity_smooth",
                "grade_smooth",
            ]

        logger.info(f"[STRAVA_CLIENT] Fetching streams for activity {activity_id} (types: {stream_types})")
        quota_manager.wait_for_slot()

        try:
            resp = httpx.get(
                f"{STRAVA_BASE_URL}/activities/{activity_id}/streams",
                headers=self._headers(),
                params={
                    "keys": ",".join(stream_types),
                    "key_by_type": "true",
                },
                timeout=15,
            )

            quota_manager.update_from_headers(dict(resp.headers))

            if resp.status_code == 404:
                logger.debug(f"[STRAVA_CLIENT] Activity {activity_id} not found or streams unavailable")
                return None

            resp.raise_for_status()

            payload = resp.json()
            if not payload:
                logger.debug(f"[STRAVA_CLIENT] No streams data for activity {activity_id}")
                return None

            # Strava returns streams as a list of stream objects
            # Convert to dict keyed by stream type for easier access
            streams_dict: dict[str, list] = {}
            for stream in payload:
                stream_type = stream.get("type")
                if stream_type:
                    streams_dict[stream_type] = stream.get("data", [])

            logger.info(
                f"[STRAVA_CLIENT] Fetched streams for activity {activity_id}: "
                f"{len(streams_dict)} types, {len(streams_dict.get('time', []))} data points"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"[STRAVA_CLIENT] Activity {activity_id} not found or streams unavailable")
                return None
            logger.error(f"[STRAVA_CLIENT] Error fetching streams for activity {activity_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"[STRAVA_CLIENT] Unexpected error fetching streams for activity {activity_id}: {e}")
            raise
        else:
            return streams_dict if streams_dict else None

    def fetch_athlete(self) -> dict:
        """Fetch authenticated athlete profile from Strava API.

        Returns:
            Dictionary containing athlete profile data from Strava API.
            Includes: id, firstname, lastname, sex, weight, city, state, country, profile (photo URL)
        """
        logger.info("[STRAVA_CLIENT] Fetching athlete profile")
        quota_manager.wait_for_slot()

        resp = httpx.get(
            f"{STRAVA_BASE_URL}/athlete",
            headers=self._headers(),
            timeout=15,
        )

        quota_manager.update_from_headers(dict(resp.headers))
        resp.raise_for_status()

        athlete_data = resp.json()
        logger.info(f"[STRAVA_CLIENT] Fetched athlete profile for athlete_id={athlete_data.get('id')}")
        return athlete_data
