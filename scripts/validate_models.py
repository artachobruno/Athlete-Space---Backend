from datetime import date, datetime

from models.activity import ActivityRecord
from models.decision import Decision
from models.training_state import TrainingState

ActivityRecord(
    athlete_id=12345,
    activity_id="1",
    source="strava",
    sport="run",
    start_time=datetime.fromisoformat("2024-01-01T07:00:00"),
    duration_sec=3600,
    distance_m=10000,
    elevation_m=150,
    avg_hr=150,
    power=None,
)

TrainingState(
    date=date.fromisoformat("2024-01-01"),
    acute_load_7d=120.0,
    chronic_load_28d=95.0,
    training_stress_balance=25.0,
    load_trend_7d="rising",
    monotony=1.4,
    recovery_status="adequate",
    readiness_score=80,
    recommended_intent="MAINTAIN",
    intensity_distribution={"easy": 0.7, "hard": 0.3},
)

Decision(
    domain="training",
    priority=1,
    recommendation="Reduce intensity tomorrow",
    rationale="Acute load exceeded chronic baseline",
    confidence=0.85,
)
