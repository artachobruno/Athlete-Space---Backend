from __future__ import annotations

import datetime as dt

from app.services.integrations.strava.client import StravaClient
from app.services.integrations.strava.oauth import exchange_code_for_token
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config.settings import settings
from ingestion.strava_ingestion import ingest_strava_activities

STRAVA_CLIENT_ID = settings.strava_client_id
STRAVA_CLIENT_SECRET = settings.strava_client_secret
STRAVA_REDIRECT_URI = settings.strava_redirect_uri

router = APIRouter()


@router.get("/strava/connect")
def strava_connect():
    url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={STRAVA_REDIRECT_URI}"
        "&scope=activity:read_all"
        "&approval_prompt=auto"
    )
    return RedirectResponse(url)


@router.get("/strava/callback", response_class=HTMLResponse)
def strava_callback(code: str):
    token_data = exchange_code_for_token(
        client_id=STRAVA_CLIENT_ID,
        client_secret=STRAVA_CLIENT_SECRET,
        code=code,
        redirect_uri=STRAVA_REDIRECT_URI,
    )
    access_token = token_data["access_token"]
    athlete_id = token_data["athlete"]["id"]

    client = StravaClient(access_token=access_token)

    records = ingest_strava_activities(
        client=client,
        athlete_id=athlete_id,
        since=dt.datetime.now(dt.UTC) - dt.timedelta(days=14),
        until=dt.datetime.now(dt.UTC),
    )

    return f"""
    <h3>Strava Connected</h3>
    <p>Ingested {len(records)} activities</p>
    """
