from models.activity import ActivityRecord
from models.training_state import TrainingState
from models.decision import Decision

ActivityRecord(
    activity_id="1",
    source="strava",
    sport="run",
    start_time="2024-01-01T07:00:00",
    duration_sec=3600,
    distance_m=10000,
    elevation_m=150
)

TrainingState(
    acute_load=120,
    chronic_load=95,
    load_trend="rising",
    monotony=1.4,
    recovery_status="adequate",
    injury_risk_flag=False,
    intensity_distribution={"easy": 0.7, "hard": 0.3}
)

Decision(
    domain="training",
    priority=1,
    recommendation="Reduce intensity tomorrow",
    rationale="Acute load exceeded chronic baseline",
    confidence=0.85
)
