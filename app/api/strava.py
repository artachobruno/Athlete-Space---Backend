from __future__ import annotations

import datetime as dt

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from sqlalchemy import select

from app.core.settings import settings
from app.ingestion.save_activities import save_activity_records
from app.ingestion.strava_ingestion import ingest_strava_activities
from app.integrations.strava.client import StravaClient
from app.integrations.strava.oauth import exchange_code_for_token
from app.integrations.strava.token_service import TokenService
from app.state.db import get_session
from app.state.models import Activity, StravaAuth

STRAVA_CLIENT_ID = settings.strava_client_id
STRAVA_CLIENT_SECRET = settings.strava_client_secret
STRAVA_REDIRECT_URI = settings.strava_redirect_uri

router = APIRouter()


@router.get("/strava/status")
def strava_status():
    """Check if Strava is connected."""
    try:
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if result:
                auth = result[0]
                # Check if activities exist
                activity_count = session.query(Activity).count()
                return {
                    "connected": True,
                    "athlete_id": auth.athlete_id,
                    "activity_count": activity_count,
                }
            return {"connected": False, "activity_count": 0}
    except Exception as e:
        logger.error(f"Error checking Strava status: {e}")
        return {"connected": False, "error": str(e), "activity_count": 0}


@router.post("/strava/sync")
def strava_sync():
    """Manually sync activities from Strava for connected account."""
    try:
        with get_session() as session:
            result = session.execute(select(StravaAuth)).first()
            if not result:
                return {"success": False, "error": "Strava not connected"}

            auth = result[0]
            athlete_id = auth.athlete_id

            # Get access token
            token_service = TokenService(session)
            token_result = token_service.get_access_token(athlete_id=athlete_id)

            # Fetch and save activities
            client = StravaClient(access_token=token_result.access_token)
            records = ingest_strava_activities(
                client=client,
                since=dt.datetime.now(dt.UTC) - dt.timedelta(days=60),
                until=dt.datetime.now(dt.UTC),
            )

            saved_count = save_activity_records(session, records)

            return {
                "success": True,
                "fetched": len(records),
                "saved": saved_count,
            }
    except Exception as e:
        logger.error(f"Error syncing Strava activities: {e}")
        return {"success": False, "error": str(e)}


@router.get("/strava/connect")
def strava_connect():
    logger.info("Strava OAuth connect initiated")
    url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={STRAVA_REDIRECT_URI}"
        "&scope=activity:read_all"
        "&approval_prompt=auto"
    )
    logger.debug(f"Redirecting to Strava OAuth: {url[:50]}...")
    return RedirectResponse(url)


@router.get("/strava/callback", response_class=HTMLResponse)
def strava_callback(code: str):
    """Handle Strava OAuth callback and persist tokens.

    After OAuth exchange, we persist only:
    - athlete_id (from athlete.id in response)
    - refresh_token
    - expires_at

    Access tokens are never persisted - they are ephemeral.
    """
    logger.info("Strava OAuth callback received")
    try:
        logger.debug("Exchanging authorization code for tokens")
        token_data = exchange_code_for_token(
            client_id=STRAVA_CLIENT_ID,
            client_secret=STRAVA_CLIENT_SECRET,
            code=code,
            redirect_uri=STRAVA_REDIRECT_URI,
        )

        # Extract data from OAuth response
        athlete_id = token_data["athlete"]["id"]
        refresh_token = token_data["refresh_token"]
        expires_at = token_data["expires_at"]
        access_token = token_data["access_token"]

        logger.info(f"OAuth successful for athlete_id={athlete_id}")

        # Persist tokens (only refresh_token and expires_at, not access_token)
        logger.debug("Saving tokens to database")
        with get_session() as session:
            token_service = TokenService(session)
            token_service.save_tokens(
                athlete_id=athlete_id,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
        logger.info("Tokens saved successfully")

        # Use access token for immediate ingestion (it's ephemeral, not persisted)
        logger.info("Starting activity ingestion")
        client = StravaClient(access_token=access_token)

        records = ingest_strava_activities(
            client=client,
            since=dt.datetime.now(dt.UTC) - dt.timedelta(days=14),
            until=dt.datetime.now(dt.UTC),
        )

        logger.info(f"Ingestion complete: {len(records)} activities fetched from Strava")

        # Save activities to database
        logger.info("Saving activities to database")
        with get_session() as session:
            saved_count = save_activity_records(session, records)
        logger.info(f"Saved {saved_count} activities to database")

        # Redirect back to Streamlit UI
        redirect_url = "http://localhost:8501"
        return f"""
        <html>
        <head>
            <title>Strava Connected</title>
            <meta http-equiv="refresh" content="3;url={redirect_url}">
        </head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #4FC3F7;">✓ Strava Connected Successfully!</h2>
            <p>Ingested {len(records)} activities</p>
            <p>Saved {saved_count} activities to database</p>
            <p><small>Redirecting to Virtus AI...</small></p>
            <p><a href="{redirect_url}">Click here if not redirected</a></p>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Error in Strava callback: {e}")
        raise
