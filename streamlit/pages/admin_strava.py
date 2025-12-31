import datetime as dt
from typing import Any

import requests

import streamlit as st

st_any: Any = st

BACKEND_URL = "http://localhost:8000"


def badge(state: str) -> str:
    return {
        "ok": "🟢 Up to date",
        "backfilling": "🔵 Backfilling",
        "stale": "🟡 Stale",
        "stuck": "🟠 Stuck",
        "error": "🔴 Error",
    }.get(state, state)


st_any.title("Strava Ingestion Admin")

# =========================
# Fetch status
# =========================
status = requests.get(
    f"{BACKEND_URL}/admin/ingestion/strava",
    timeout=5,
).json()

# =========================
# Quota
# =========================
st_any.subheader("Strava Quota")

quota = status["quota"]

col1, col2 = st_any.columns(2)
col1.metric("15m Used", quota.get("used_15m") or 0)
col2.metric("Daily Used", quota.get("used_daily") or 0)

st_any.divider()

# =========================
# User Ingestion Status
# =========================
st_any.subheader("User Ingestion Status")

rows = []
now = int(dt.datetime.now(dt.UTC).timestamp())

for u in status["users"]:
    last_ingest = u["last_ingested_at"]

    rows.append({
        "athlete_id": u["athlete_id"],
        "state": badge(u["state"]),
        "last_ingest_min_ago": (None if not last_ingest else int((now - last_ingest) / 60)),
        "backfill_page": u["backfill_page"],
        "backfill_done": u["backfill_done"],
        "last_error": u["last_error"],
    })

st_any.dataframe(rows, use_container_width=True)

st_any.divider()

# =========================
# Retry Single User
# =========================
st_any.subheader("Retry User")

athlete_ids = [u["athlete_id"] for u in status["users"]]

if athlete_ids:
    selected = st_any.selectbox("Athlete ID", athlete_ids)

    if st_any.button("Retry ingestion for user"):
        resp = requests.post(
            f"{BACKEND_URL}/admin/retry/strava/{selected}",
            timeout=5,
        ).json()
        st_any.success(f"Enqueued retry for athlete {resp['athlete_id']}")
else:
    st_any.info("No Strava users available")

st_any.divider()

# =========================
# Recent Activities
# =========================
st_any.subheader("Recent Strava Activities")

activities = requests.get(
    f"{BACKEND_URL}/admin/activities/recent?limit=10",
    timeout=5,
).json()

if activities:
    st_any.table(activities)
else:
    st_any.info("No Strava activities ingested yet")
