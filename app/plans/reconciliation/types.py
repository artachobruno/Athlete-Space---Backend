"""HR-based pace reconciliation input/output models.

This module defines the data structures for:
- Executed workout data (from Strava/Garmin/manual logs)
- Reconciliation results (planned vs observed effort comparison)
"""

from typing import Literal, Optional

from pydantic import BaseModel

from app.plans.types import WorkoutIntent


class ExecutedWorkout(BaseModel):
    """Executed workout data from activity ingestion.

    This is the bridge from Strava/Garmin/manual logs to reconciliation.
    All fields are optional to handle incomplete data gracefully.

    Attributes:
        planned_session_id: ID of the planned session that was executed (string UUID)
        actual_distance_miles: Actual distance covered in miles
        actual_duration_min: Actual duration in minutes
        avg_hr: Average heart rate in bpm
        max_hr: Maximum heart rate in bpm
        avg_pace_min_per_mile: Average pace in minutes per mile
    """

    planned_session_id: str
    actual_distance_miles: float | None = None
    actual_duration_min: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_pace_min_per_mile: float | None = None


class ReconciliationResult(BaseModel):
    """Reconciliation result comparing planned vs executed effort.

    This is a read-only observation - it does NOT modify plans.
    Provides explainable mismatch detection and recommendations.

    Attributes:
        planned_intent: The planned workout intent (rest, easy, long, quality)
        planned_pace: Planned pace in minutes per mile (if available)
        observed_pace: Observed pace in minutes per mile (if available)
        hr_zone: Observed HR zone based on avg_hr (if available)
        effort_mismatch: Classification of effort mismatch
        recommendation: Human-readable recommendation (if mismatch detected)
    """

    planned_intent: WorkoutIntent
    planned_pace: float | None = None
    observed_pace: float | None = None
    hr_zone: str | None = None

    effort_mismatch: Literal[
        "too_easy",
        "on_target",
        "too_hard",
        "unknown",
    ]

    recommendation: str | None = None
