import os
import time
from datetime import datetime, timezone
from typing import Any

import altair as alt
import pandas as pd
import requests
from PIL import Image

import streamlit as st

st_any: Any = st

# Get backend URL from environment variable or use default
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Track API call timing
if "api_call_times" not in st_any.session_state:
    st_any.session_state.api_call_times = {
        "status": [],
        "overview": [],
        "coach": [],
    }

# -------------------------------------------------
# Session State
# -------------------------------------------------
if "last_sync_time" not in st_any.session_state:
    st_any.session_state.last_sync_time = None
if "sync_in_progress" not in st_any.session_state:
    st_any.session_state.sync_in_progress = False
if "selected_days" not in st_any.session_state:
    st_any.session_state.selected_days = 60
if "coach_chat" not in st_any.session_state:
    st_any.session_state.coach_chat = []
if "coach_input" not in st_any.session_state:
    st_any.session_state.coach_input = ""


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def submit_coach_message() -> None:
    user_text = st_any.session_state.coach_input.strip()
    if not user_text:
        return

    st_any.session_state.coach_chat.append({"role": "user", "content": user_text})

    with st_any.spinner("Virtus is thinking..."):
        resp = requests.post(
            f"{BACKEND_URL}/coach/chat",
            json={"message": user_text, "days": st_any.session_state.selected_days},
            timeout=20,
        ).json()

    reply = resp.get("reply") or resp.get("message") or resp.get("output") or "No response from coach."

    st_any.session_state.coach_chat.append({"role": "assistant", "content": reply})

    st_any.session_state.coach_input = ""


def send_quick_action(prompt: str) -> None:
    st_any.session_state.coach_input = prompt
    submit_coach_message()


def coach_row(label: str, value: str) -> None:
    st_any.markdown(
        f"""
        <div style="
            display:flex;
            justify-content:space-between;
            padding:6px 0;
            font-size:0.85rem;
            color:#C9CDD6;
            border-bottom:1px solid #1E2230;
        ">
            <span>{label}</span>
            <strong style="color:#FFFFFF">{value}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )


# -------------------------------------------------
# Page config
# -------------------------------------------------
st_any.set_page_config(
    page_title="Virtus AI",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------------------------------------
# Styling
# -------------------------------------------------
st_any.markdown(
    """
    <style>
    body { background-color:#0E1117; color:#FAFAFA; }
    section[data-testid="stSidebar"] { background-color:#0B0F16; }

    div[data-testid="metric-container"] {
        background-color: transparent;
        border: 1px solid #1E2230;
        border-radius: 6px;
        padding: 0.75rem;
    }
    div[data-testid="metric-label"] {
        font-size: 0.75rem;
        color: #9AA0AE;
    }
    div[data-testid="metric-value"] {
        font-size: 1.4rem;
        font-weight: 600;
    }

    button {
        background-color:#0E1117 !important;
        border:1px solid #1E2230 !important;
        color:#C9CDD6 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------------------------------
# Sidebar
# -------------------------------------------------
with st_any.sidebar:
    st_any.image(Image.open("ui/assets/virtus_logo.png"), width=180)
    st_any.markdown("### Virtus AI")
    st_any.caption("Performance Intelligence")
    st_any.divider()

    page = st_any.radio(
        "Navigation",
        ["Overview", "Training", "Calendar", "Coach", "Debug"],
        label_visibility="collapsed",
    )

# -------------------------------------------------
# Header
# -------------------------------------------------
left, right = st_any.columns([3, 1])

with left:
    st_any.markdown("## Athlete Overview")

    try:
        call_time = time.time()
        status = requests.get(f"{BACKEND_URL}/me/status", timeout=2).json()
        elapsed = time.time() - call_time

        # Track timing
        st_any.session_state.api_call_times["status"].append(call_time)
        if len(st_any.session_state.api_call_times["status"]) > 10:
            st_any.session_state.api_call_times["status"].pop(0)

        # Calculate refresh rate
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        if len(st_any.session_state.api_call_times["status"]) >= 2:
            time_diff = st_any.session_state.api_call_times["status"][-1] - st_any.session_state.api_call_times["status"][-2]
            print(f"[UI] /me/status called at {now_str} - elapsed: {elapsed:.3f}s - refresh_rate: {time_diff:.2f}s since last call")
        else:
            print(f"[UI] /me/status called at {now_str} - elapsed: {elapsed:.3f}s")

        connected = status.get("connected", False)
        sync_state = status.get("state", "unknown")
        last_sync = status.get("last_sync")
    except Exception as e:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        print(f"[UI] /me/status ERROR at {now_str}: {e}")
        connected = False
        sync_state = "error"
        last_sync = None

    label = {
        "ok": "Synced",
        "syncing": "Syncing",
        "stale": "Stale",
        "error": "Error",
    }.get(sync_state, sync_state)

    st_any.caption(f"Strava · {'Connected' if connected else 'Not connected'} · {label}")

with right:
    if last_sync:
        from datetime import datetime

        sync_time = datetime.fromisoformat(last_sync.replace("Z", "+00:00")).strftime("%b %d, %H:%M")
    else:
        sync_time = "Never"

    st_any.caption(f"Last sync · {sync_time}")

st_any.divider()

# -------------------------------------------------
# Time Window
# -------------------------------------------------
st_any.session_state.selected_days = st_any.radio(
    "Time Window",
    [30, 60, 90],
    index=[30, 60, 90].index(st_any.session_state.selected_days),
    horizontal=True,
    label_visibility="collapsed",
)

# -------------------------------------------------
# Load Data
# -------------------------------------------------
call_time = time.time()
overview = requests.get(f"{BACKEND_URL}/me/overview", timeout=5).json()
elapsed = time.time() - call_time

# Track timing
st_any.session_state.api_call_times["overview"].append(call_time)
if len(st_any.session_state.api_call_times["overview"]) > 10:
    st_any.session_state.api_call_times["overview"].pop(0)

# Calculate refresh rate
now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
if len(st_any.session_state.api_call_times["overview"]) >= 2:
    time_diff = st_any.session_state.api_call_times["overview"][-1] - st_any.session_state.api_call_times["overview"][-2]
    print(f"[UI] /me/overview called at {now_str} - elapsed: {elapsed:.3f}s - refresh_rate: {time_diff:.2f}s since last call")
else:
    print(f"[UI] /me/overview called at {now_str} - elapsed: {elapsed:.3f}s")

metrics = overview.get("metrics", {})
today = overview.get("today", {})
data_quality_status = overview.get("data_quality", "insufficient")

# Extract metrics lists
ctl_list = metrics.get("ctl", [])
atl_list = metrics.get("atl", [])
tsb_list = metrics.get("tsb", [])

# Debug: print what we're getting
print(f"[UI] Metrics debug: ctl={len(ctl_list)} items, atl={len(atl_list)} items, tsb={len(tsb_list)} items")
print(f"[UI] Data quality: {data_quality_status}")
if ctl_list:
    print(f"[UI] First CTL entry: {ctl_list[0]}, Last CTL entry: {ctl_list[-1]}")
    print(f"[UI] First ATL entry: {atl_list[0] if atl_list else 'N/A'}, Last ATL entry: {atl_list[-1] if atl_list else 'N/A'}")
    print(f"[UI] First TSB entry: {tsb_list[0] if tsb_list else 'N/A'}, Last TSB entry: {tsb_list[-1] if tsb_list else 'N/A'}")

# Create DataFrame - ensure all lists have the same length
min_length = min(len(ctl_list), len(atl_list), len(tsb_list))
if min_length == 0:
    df = pd.DataFrame({"date": [], "CTL": [], "ATL": [], "TSB": []})
    df["date"] = pd.to_datetime(df["date"])
    print("[UI] WARNING: All metrics lists are empty - creating empty DataFrame")
else:
    # Use the minimum length to ensure all columns have the same number of rows
    if len(ctl_list) != len(atl_list) or len(atl_list) != len(tsb_list):
        print(f"[UI] WARNING: Metrics lists have different lengths! Truncating to minimum length: {min_length}")
        ctl_list = ctl_list[:min_length]
        atl_list = atl_list[:min_length]
        tsb_list = tsb_list[:min_length]

    df = pd.DataFrame({
        "date": pd.to_datetime([d for d, _ in ctl_list]),
        "CTL": [v for _, v in ctl_list],
        "ATL": [v for _, v in atl_list],
        "TSB": [v for _, v in tsb_list],
    })

    # Debug: check if DataFrame is empty
    if df.empty:
        print("[UI] WARNING: DataFrame is empty after creation!")
    else:
        print(f"[UI] DataFrame created successfully: {len(df)} rows, date range: {df['date'].min()} to {df['date'].max()}")

# -------------------------------------------------
# KPI
# -------------------------------------------------
k1, k2, k3, k4 = st_any.columns(4)
k1.metric("CTL", f"{today.get('ctl', 0):.1f}")
k2.metric("ATL", f"{today.get('atl', 0):.1f}")
k3.metric("TSB", f"{today.get('tsb', 0):.1f}")
k4.metric("Data", data_quality_status)

# -------------------------------------------------
# Layout
# -------------------------------------------------
main, side = st_any.columns([2, 1])

with main:
    if df.empty:
        metrics_info = f"ctl={len(ctl_list)}, atl={len(atl_list)}, tsb={len(tsb_list)}"
        st_any.warning(f"No training data available for chart. Data quality: {data_quality_status}. Metrics: {metrics_info}")
        st_any.info("Training data will appear here once you have at least 14 days of activity data.")
    else:
        chart = (
            alt.Chart(df)
            .transform_fold(["CTL", "ATL", "TSB"], as_=["metric", "value"])
            .mark_line(strokeWidth=2)
            .encode(
                x=alt.X("date:T", axis=alt.Axis(grid=False)),
                y=alt.Y("value:Q", axis=alt.Axis(grid=True, gridColor="#1E2230")),
                color=alt.Color(
                    "metric:N",
                    scale=alt.Scale(
                        domain=["CTL", "ATL", "TSB"],
                        range=["#5B8DEF", "#9AA0AE", "#7A869A"],
                    ),
                    legend=alt.Legend(title=None),
                ),
            )
            .properties(height=300)
        )
        st_any.altair_chart(chart, use_container_width=True)

# -------------------------------------------------
# Coach Panel
# -------------------------------------------------
with side:
    st_any.markdown("### Virtus Coach")
    st_any.caption("Adaptive performance guidance")

    call_time = time.time()
    snap = requests.get(f"{BACKEND_URL}/state/coach", timeout=10).json()
    elapsed = time.time() - call_time

    # Track timing
    st_any.session_state.api_call_times["coach"].append(call_time)
    if len(st_any.session_state.api_call_times["coach"]) > 10:
        st_any.session_state.api_call_times["coach"].pop(0)

    # Calculate refresh rate
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    if len(st_any.session_state.api_call_times["coach"]) >= 2:
        time_diff = st_any.session_state.api_call_times["coach"][-1] - st_any.session_state.api_call_times["coach"][-2]
        print(f"[UI] /state/coach called at {now_str} - elapsed: {elapsed:.3f}s - refresh_rate: {time_diff:.2f}s since last call")
    else:
        print(f"[UI] /state/coach called at {now_str} - elapsed: {elapsed:.3f}s")

    insights = snap.get("insights", [])
    recommendations = snap.get("recommendations", [])

    coach_row("State", insights[0] if insights else "No insights available")
    coach_row("Risk", snap.get("risk_level", "unknown").upper())
    coach_row("Focus", recommendations[0] if recommendations else "No recommendations")

    st_any.divider()

    qa1, qa2, qa3 = st_any.columns(3)
    with qa1:
        st_any.button("Today's session", on_click=send_quick_action, args=("What session should I do today?",))
    with qa2:
        st_any.button("Fatigue check", on_click=send_quick_action, args=("Am I accumulating fatigue or adapting well?",))
    with qa3:
        st_any.button("Adjust week", on_click=send_quick_action, args=("How should I adjust my training this week?",))

    st_any.divider()

    for msg in st_any.session_state.coach_chat:
        speaker = "You" if msg["role"] == "user" else "Virtus"
        st_any.markdown(f"**{speaker}**  \n{msg['content']}")

    st_any.text_input(
        "Ask about training, fatigue, or race prep",
        key="coach_input",
        on_change=submit_coach_message,
    )

# -------------------------------------------------
# Routing placeholder
# -------------------------------------------------
if page != "Overview":
    st_any.warning(f"{page} view coming next")
