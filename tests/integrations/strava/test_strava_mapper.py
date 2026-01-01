import datetime as dt

from integrations.strava.mapper import map_strava_activity
from integrations.strava.models import StravaActivity


def test_strava_activity_mapping():
    raw = StravaActivity(
        id=123,
        type="Run",
        start_date=dt.datetime(2025, 1, 10, tzinfo=dt.UTC),
        elapsed_time=3600,
        distance=10000,
        total_elevation_gain=150,
        average_heartrate=145,
        average_watts=280,
    )

    record = map_strava_activity(raw, athlete_id=12345)

    assert record.athlete_id == 12345
    assert record.activity_id == "strava-123"
    assert record.source == "strava"
    assert record.sport == "run"
    assert record.duration_sec == 3600
    assert record.avg_hr == 145
    assert record.power["avg_watts"] == 280
