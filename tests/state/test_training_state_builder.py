import datetime as dt

from models.activity import ActivityRecord
from state.training_state_builder import build_training_state


def make_activity(
    *,
    days_ago: int,
    duration_min: int = 60,
    avg_hr: int | None = 140,
) -> ActivityRecord:
    return ActivityRecord(
        activity_id=f"a-{days_ago}",
        source="strava",
        sport="run",
        start_time=dt.datetime(2025, 1, 10, tzinfo=dt.UTC) - dt.timedelta(days=days_ago),
        duration_sec=duration_min * 60,
        distance_m=10_000,
        elevation_m=100,
        avg_hr=avg_hr,
        power=None,
    )


def test_acute_and_chronic_load_computation():
    today = dt.date(2025, 1, 10)

    activities = [make_activity(days_ago=i) for i in range(7)]

    state = build_training_state(
        activities=activities,
        today=today,
        prev_state=None,
    )

    assert state.acute_load_7d > 0
    assert state.chronic_load_28d > 0
    assert isinstance(state.training_stress_balance, float)


def test_load_trend_rising():
    today = dt.date(2025, 1, 10)

    activities = [
        make_activity(days_ago=6, duration_min=30),
        make_activity(days_ago=5, duration_min=35),
        make_activity(days_ago=4, duration_min=40),
        make_activity(days_ago=3, duration_min=45),
        make_activity(days_ago=2, duration_min=50),
        make_activity(days_ago=1, duration_min=60),
    ]

    state = build_training_state(activities=activities, today=today)

    assert state.load_trend_7d == "rising"


def test_high_monotony_flag_triggered():
    today = dt.date(2025, 1, 10)

    activities = [make_activity(days_ago=i, duration_min=60) for i in range(7)]

    state = build_training_state(activities=activities, today=today)

    assert "HIGH_MONOTONY" in state.risk_flags
    assert state.monotony >= 2.0


def test_acute_spike_flag_triggered():
    today = dt.date(2025, 1, 10)

    base_activities = [make_activity(days_ago=i + 7, duration_min=30) for i in range(7)]
    prev_state = build_training_state(
        activities=base_activities,
        today=today - dt.timedelta(days=7),
    )

    spike_activities = [make_activity(days_ago=i, duration_min=120) for i in range(7)]

    state = build_training_state(
        activities=spike_activities,
        today=today,
        prev_state=prev_state,
    )

    assert "ACUTE_SPIKE" in state.risk_flags


def test_overreaching_flag():
    today = dt.date(2025, 1, 10)

    activities = [make_activity(days_ago=i, duration_min=120) for i in range(7)]

    state = build_training_state(activities=activities, today=today)

    assert "OVERREACHING" in state.risk_flags


def test_recovery_status_and_readiness_bounds():
    today = dt.date(2025, 1, 10)

    activities = [make_activity(days_ago=i, duration_min=45) for i in range(7)]

    state = build_training_state(activities=activities, today=today)

    assert state.recovery_status in {"under", "adequate", "over"}
    assert 0 <= state.readiness_score <= 100


def test_recommended_intent_consistency():
    today = dt.date(2025, 1, 10)

    activities = [make_activity(days_ago=i, duration_min=30) for i in range(7)]

    state = build_training_state(activities=activities, today=today)

    assert state.recommended_intent in {"RECOVER", "MAINTAIN", "BUILD"}
