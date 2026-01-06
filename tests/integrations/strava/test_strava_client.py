import datetime as dt

import httpx

from app.integrations.strava.client import StravaClient


def test_fetch_activities_handles_empty_response(monkeypatch):
    def mock_get(*args, **kwargs):
        request = httpx.Request("GET", "https://www.strava.com/api/v3/athlete/activities")
        return httpx.Response(200, json=[], request=request)

    monkeypatch.setattr(httpx, "get", mock_get)

    client = StravaClient(access_token="test_token")

    activities = client.fetch_recent_activities(
        after=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
    )

    assert activities == []
