import datetime as dt

from app.state.models import ActivityRecord
from pipeline.training_pipeline import StubTrainingAgent, run_training_pipeline


def make_activity(
    *,
    days_ago: int,
    duration_min: int = 60,
    avg_hr: int | None = 140,
    athlete_id: int = 1,
) -> ActivityRecord:
    return ActivityRecord(
        athlete_id=athlete_id,
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


def test_pipeline_runs_end_to_end_with_stub_agent():
    today = dt.date(2025, 1, 10)
    activities = [make_activity(days_ago=i) for i in range(7)]

    state, decision = run_training_pipeline(
        activities=activities,
        today=today,
        prev_state=None,
    )

    # --- State checks ---
    assert state.date == today
    assert state.acute_load_7d > 0
    assert state.chronic_load_28d >= 0

    # --- Decision checks ---
    assert decision.recommended_intent == state.recommended_intent
    assert isinstance(decision.explanation, str)
    assert "deterministic" in decision.explanation.lower()


def test_pipeline_accepts_custom_agent():
    class AlwaysRecoverAgent:
        def decide(self, *, state):
            return type(
                "Decision",
                (),
                {
                    "recommended_intent": "RECOVER",
                    "explanation": "Forced recovery for testing.",
                },
            )()

    today = dt.date(2025, 1, 10)
    activities = [make_activity(days_ago=i) for i in range(7)]

    _state, decision = run_training_pipeline(
        activities=activities,
        today=today,
        agent=AlwaysRecoverAgent(),
    )

    assert decision.recommended_intent == "RECOVER"
    assert "Forced recovery" in decision.explanation


def test_pipeline_returns_decision_even_with_empty_history():
    today = dt.date(2025, 1, 10)

    state, decision = run_training_pipeline(
        activities=[],
        today=today,
        agent=StubTrainingAgent(),
    )

    assert state.date == today
    assert decision.recommended_intent in {"RECOVER", "MAINTAIN", "BUILD"}
