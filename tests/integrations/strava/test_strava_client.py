import datetime as dt

import httpx

from integrations.strava.client import StravaClient


def test_fetch_activities_handles_empty_response(monkeypatch):
    def mock_get(*args, **kwargs):
        request = httpx.Request("GET", "https://www.strava.com/api/v3/athlete/activities")
        return httpx.Response(200, json=[], request=request)

    monkeypatch.setattr(httpx, "get", mock_get)

    client = StravaClient(
        access_token="x",
        refresh_token="y",
        client_id="id",
        client_secret="secret",
    )

    activities = client.fetch_activities(
        since=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
        until=dt.datetime(2025, 1, 10, tzinfo=dt.UTC),
    )

    assert activities == []
