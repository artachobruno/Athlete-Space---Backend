import datetime as dt

from app.integrations.strava.client import StravaClient
from app.integrations.strava.schemas import StravaActivity
from ingestion.strava_ingestion import ingest_strava_activities


class FakeStravaClient(StravaClient):
    def __init__(self) -> None:
        pass

    def fetch_activities(
        self,
        *,
        since: dt.datetime,
        until: dt.datetime,
        per_page: int = 50,
    ) -> list[StravaActivity]:
        return [
            StravaActivity(
                id=1,
                type="Run",
                start_date=dt.datetime(2025, 1, 10, tzinfo=dt.UTC),
                elapsed_time=3600,
                distance=10000,
                total_elevation_gain=120,
                average_heartrate=150,
                average_watts=None,
            )
        ]


def test_strava_ingestion_maps_to_activity_records():
    records = ingest_strava_activities(
        client=FakeStravaClient(),
        athlete_id=12345,
        since=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
        until=dt.datetime(2025, 1, 10, tzinfo=dt.UTC),
    )

    assert len(records) == 1
    assert records[0].source == "strava"
    assert records[0].sport == "run"
