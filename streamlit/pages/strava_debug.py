from typing import Any

import requests

import streamlit as st

st_any: Any = st

BACKEND_URL = "http://localhost:8000"

st_any.subheader("Recent Strava Activities")

rows = requests.get(
    f"{BACKEND_URL}/admin/activities/recent?limit=10",
    timeout=5,
).json()

if rows:
    st_any.table(rows)
else:
    st_any.info("No Strava activities ingested yet")
