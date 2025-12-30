from __future__ import annotations

import altair as alt
import pandas as pd
import requests
import streamlit as st
from PIL import Image

BACKEND_URL = "http://localhost:8000"

# -------------------------------------------------
# Session State
# -------------------------------------------------
if "last_sync_time" not in st.session_state:
    st.session_state.last_sync_time = None
if "sync_in_progress" not in st.session_state:
    st.session_state.sync_in_progress = False
if "selected_days" not in st.session_state:
    st.session_state.selected_days = 60
if "coach_chat" not in st.session_state:
    st.session_state.coach_chat = []
if "coach_input" not in st.session_state:
    st.session_state.coach_input = ""


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def submit_coach_message() -> None:
    user_text = st.session_state.coach_input.strip()
    if not user_text:
        return

    # append user message
    st.session_state.coach_chat.append({"role": "user", "content": user_text})

    # call backend LLM
    with st.spinner("Virtus is thinking..."):
        resp = requests.post(
            f"{BACKEND_URL}/coach/chat",
            json={
                "message": user_text,
                "days": st.session_state.selected_days,
            },
            timeout=20,
        ).json()

    reply = resp.get("reply") or resp.get("message") or resp.get("output") or "No response from coach."

    st.session_state.coach_chat.append({
        "role": "assistant",
        "content": f"{reply}\n\n_(intent: {resp.get('intent', 'unknown')})_",
    })

    # SAFE reset
    st.session_state.coach_input = ""


# -------------------------------------------------
# Page config
# -------------------------------------------------
st.set_page_config(
    page_title="Virtus AI",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------------------------------------
# Styling
# -------------------------------------------------
st.markdown(
    """
    <style>
    body { background-color: #0E1117; color: #FAFAFA; }
    section[data-testid="stSidebar"] { background-color: #0B0F16; }
    div[data-testid="metric-container"] {
        background-color: #161A23;
        border-radius: 10px;
        padding: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------------------------------
# Sidebar
# -------------------------------------------------
with st.sidebar:
    st.image(Image.open("ui/assets/virtus_logo.png"), width=180)
    st.markdown("### Virtus AI")
    st.markdown("**Performance Intelligence**")
    st.divider()

    page = st.radio(
        "Navigation",
        ["Overview", "Training", "Calendar", "Coach", "Debug"],
        label_visibility="collapsed",
    )

# -------------------------------------------------
# Header
# -------------------------------------------------
left, right = st.columns([3, 1])

with left:
    st.markdown("## Athlete Overview")

    try:
        status = requests.get(f"{BACKEND_URL}/strava/status", timeout=2).json()
        connected = status.get("connected", False)
        activity_count = status.get("activity_count", 0)
    except Exception:
        connected = False
        activity_count = 0

    if connected:
        st.markdown(
            f"<span style='color:#4FC3F7'>Connected • Strava ✓ • {activity_count} activities</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<span style='color:#EF5350'>Not Connected • Strava ✗</span>",
            unsafe_allow_html=True,
        )

with right:
    sync_time = st.session_state.last_sync_time.strftime("%b %d, %H:%M") if st.session_state.last_sync_time else "Never"
    st.markdown(
        f"<div style='text-align:right; font-size:0.85rem; color:#A0A0A0;'>Last sync<br/>{sync_time}</div>",
        unsafe_allow_html=True,
    )

st.divider()

# -------------------------------------------------
# Time Window
# -------------------------------------------------
selected_days = st.radio(
    "Time Window",
    [30, 60, 90],
    index=[30, 60, 90].index(st.session_state.selected_days),
    horizontal=True,
    label_visibility="collapsed",
)
st.session_state.selected_days = selected_days

# -------------------------------------------------
# Load Training Data
# -------------------------------------------------
try:
    resp = requests.get(
        f"{BACKEND_URL}/state/training-load?days={selected_days}",
        timeout=5,
    )
    resp.raise_for_status()
    data = resp.json()
except requests.exceptions.RequestException as e:
    st.error("Backend not reachable. Is the FastAPI server running on :8000?")
    st.caption(str(e))
    data = {
        "dates": [],
        "ctl": [],
        "atl": [],
        "tsb": [],
        "daily_load": [],
    }

df = pd.DataFrame({
    "date": pd.to_datetime(data["dates"]),
    "CTL": data["ctl"],
    "ATL": data["atl"],
    "TSB": data["tsb"],
    "daily_load": data["daily_load"],
})

# -------------------------------------------------
# KPI Cards
# -------------------------------------------------
tsb_today = round(df["TSB"].iloc[-1], 1) if len(df) else 0.0

k1, k2, k3, k4 = st.columns(4)
k1.metric("Readiness", "Good" if tsb_today > -5 else "Caution", f"{tsb_today:+}")
k2.metric("7-day Load", f"{df['daily_load'].tail(7).sum():.1f} h")
k3.metric("7-day Volume", f"{df['daily_load'].tail(7).sum():.1f} h")
k4.metric("Recovery", "On Track" if tsb_today > -10 else "At Risk")

# -------------------------------------------------
# Layout
# -------------------------------------------------
main, side = st.columns([2, 1])

# =============================
# MAIN CHART
# =============================
with main:
    chart = (
        alt.Chart(df)
        .transform_fold(["CTL", "ATL", "TSB"], as_=["metric", "value"])
        .mark_line(strokeWidth=2)
        .encode(
            x="date:T",
            y="value:Q",
            color=alt.Color(
                "metric:N",
                scale=alt.Scale(
                    domain=["CTL", "ATL", "TSB"],
                    range=["#4FC3F7", "#EF5350", "#66BB6A"],
                ),
            ),
        )
        .properties(height=300)
    )
    st.altair_chart(chart, use_container_width=True)

# =============================
# COACH PANEL + CHAT
# =============================
with side:
    st.markdown("### Virtus Coach")

    # --- Snapshot ---
    try:
        snap_resp = requests.get(
            f"{BACKEND_URL}/state/coach?days={selected_days}",
            timeout=10,
        )
        snap_resp.raise_for_status()
        snapshot = snap_resp.json()

        st.markdown(f"**{snapshot.get('summary', 'No summary available')}**")
        for i in snapshot.get("insights", []):
            st.markdown(f"- {i}")

    except requests.exceptions.RequestException as e:
        st.warning("Coach snapshot unavailable (backend offline).")
        st.caption(str(e))

    st.divider()
    st.markdown("#### Ask the Coach")

    # --- Chat history ---
    for msg in st.session_state.coach_chat:
        speaker = "You" if msg["role"] == "user" else "Virtus"
        st.markdown(f"**{speaker}:** {msg['content']}")

    # --- Input ---
    st.text_input(
        "Ask about training, fatigue, race prep, or planning",
        key="coach_input",
        on_change=submit_coach_message,
    )

# -------------------------------------------------
# Placeholder routing
# -------------------------------------------------
if page != "Overview":
    st.warning(f"{page} view coming next")
