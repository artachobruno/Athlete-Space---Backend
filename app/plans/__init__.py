"""Plans module - unit-safe, pace-aware planning foundation.

This module provides:
- Canonical workout metrics with miles-only distance
- Pace estimation from race goal pace
- Volume computation in miles
- Week planning utilities

See README.md for critical rules and usage examples.
"""

from app.plans.intent_rules import get_allowed_zones_for_intent
from app.plans.pace import estimate_pace, get_zone_from_pace
from app.plans.types import PaceMetrics, WorkoutIntent, WorkoutMetrics
from app.plans.validators import validate_workout_intent, validate_workout_metrics
from app.plans.volume import compute_weekly_volume_miles
from app.plans.week_planner import (
    assign_intent_from_day_type,
    create_workout_metrics,
    get_target_weekly_volume_miles,
    infer_intent_from_session_type,
)

__all__ = [
    "PaceMetrics",
    "WorkoutIntent",
    "WorkoutMetrics",
    "assign_intent_from_day_type",
    "compute_weekly_volume_miles",
    "create_workout_metrics",
    "estimate_pace",
    "get_allowed_zones_for_intent",
    "get_target_weekly_volume_miles",
    "get_zone_from_pace",
    "infer_intent_from_session_type",
    "validate_workout_intent",
    "validate_workout_metrics",
]
