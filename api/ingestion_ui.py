from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

from app.integrations.strava.client import StravaClient
from ingestion.strava_ingestion import ingest_strava_activities

router = APIRouter()


@router.get("/ingestion", response_class=HTMLResponse)
def ingestion_page() -> str:
    return """
    <html>
      <head>
        <title>Virtus AI - Strava Ingestion</title>
        <script src="https://unpkg.com/htmx.org@1.9.10"></script>
      </head>
      <body>
        <h2>Strava Ingestion Test</h2>

        <form
          hx-post="/ingestion/strava"
          hx-target="#results"
          hx-swap="innerHTML"
        >
          <label>Access Token</label><br/>
          <input type="password" name="access_token" required style="width: 400px;" /><br/>
          <small>(Token is never stored; used once for ingestion)</small><br/><br/>

          <label>
            <input type="checkbox" name="debug" value="true" />
            Show raw data
          </label>
          <br/><br/>

          <button type="submit">Ingest Activities</button>
        </form>

        <hr/>
        <div id="results"></div>
      </body>
    </html>
    """


@router.post("/ingestion/strava", response_class=HTMLResponse)
def ingest_strava(
    access_token: str = Form(...),
    debug: bool = Form(False),
) -> str:
    try:
        client = StravaClient(
            access_token=access_token,
            refresh_token="",
            client_id="",
            client_secret="",
        )

        # For test UI, use placeholder athlete_id (0)
        # In production, athlete_id should come from authenticated user
        records = ingest_strava_activities(
            client=client,
            athlete_id=0,  # Placeholder for test endpoint
            since=dt.datetime.now(dt.UTC) - dt.timedelta(days=14),
            until=dt.datetime.now(dt.UTC),
        )

    except Exception as e:
        return f"""
        <div style="color: red;">
            <b>Error during ingestion</b><br/>
            <pre>{e}</pre>
        </div>
        """

    if not records:
        return "<p>No activities found.</p>"

    rows = "".join(
        f"""
        <tr>
          <td>{r.sport}</td>
          <td>{r.start_time}</td>
          <td>{r.distance_m / 1000:.1f} km</td>
        </tr>
        """
        for r in records
    )

    debug_block = ""
    if debug:
        debug_block = f"""
        <h4>Raw Records</h4>
        <pre style="max-height: 400px; overflow: auto;">
{json.dumps([r.model_dump() for r in records], indent=2, default=str)}
        </pre>
        """

    return f"""
    <h3>Ingested {len(records)} activities</h3>

    <table border="1" cellpadding="6">
      <tr>
        <th>Sport</th>
        <th>Date</th>
        <th>Distance</th>
      </tr>
      {rows}
    </table>

    {debug_block}
    """
