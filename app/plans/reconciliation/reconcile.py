"""HR-based pace reconciliation logic.

This module provides the core reconciliation function that compares
planned workout intent vs observed HR zone to detect effort mismatches.

This is observation + interpretation only - no plan mutations.
"""

from typing import Literal

from app.athletes.models import AthletePaceProfile
from app.db.models import PlannedSession
from app.plans.reconciliation.hr import map_hr_to_zone
from app.plans.reconciliation.types import ExecutedWorkout, ReconciliationResult
from app.plans.types import WorkoutIntent


def reconcile_workout(
    planned_session: PlannedSession,
    executed: ExecutedWorkout,
    athlete_pace_profile: AthletePaceProfile | None,
) -> ReconciliationResult:
    """Reconcile planned workout vs executed workout using HR data.

    Logic (explicit, no guessing):
    1. Read planned_session.intent
    2. Read planned_session metrics (pace if available)
    3. Compute observed HR zone from executed.avg_hr
    4. Compare intent vs HR zone
    5. Generate recommendation if mismatch detected

    Args:
        planned_session: The planned session that was executed
        executed: Executed workout data with HR metrics
        athlete_pace_profile: Athlete pace profile with hr_zones (optional)

    Returns:
        ReconciliationResult with mismatch classification and recommendation
    """
    # Extract planned intent
    intent_str = planned_session.intent or "easy"
    if intent_str == "rest":
        planned_intent: WorkoutIntent = "rest"
    elif intent_str == "easy":
        planned_intent = "easy"
    elif intent_str == "long":
        planned_intent = "long"
    elif intent_str == "quality":
        planned_intent = "quality"
    else:
        planned_intent = "easy"

    # Extract planned pace if available
    planned_pace: float | None = None
    if planned_session.distance_mi and planned_session.duration_minutes:
        # Calculate pace from distance and duration
        planned_pace = planned_session.duration_minutes / planned_session.distance_mi
    elif planned_session.distance_mi and planned_session.distance_km:
        # Fallback to km if miles not available
        distance_miles = planned_session.distance_km / 1.60934
        if planned_session.duration_minutes:
            planned_pace = planned_session.duration_minutes / distance_miles

    # Extract observed pace
    observed_pace = executed.avg_pace_min_per_mile

    # Map HR to zone if HR data and profile available
    hr_zone: str | None = None
    if executed.avg_hr is not None and athlete_pace_profile and athlete_pace_profile.hr_zones:
        hr_zone = map_hr_to_zone(executed.avg_hr, athlete_pace_profile.hr_zones)

    # Compare intent vs HR zone
    effort_mismatch: Literal["too_easy", "on_target", "too_hard", "unknown"] = "unknown"
    recommendation: str | None = None

    if hr_zone and hr_zone != "unknown":
        if planned_intent == "easy":
            # Easy runs should be in lower zones (z1, z2, lt1)
            if hr_zone in {"lt2", "threshold", "tempo", "vo2max"}:
                effort_mismatch = "too_hard"
                recommendation = "Easy pace appears too fast; consider slowing easy runs."
            elif hr_zone in {"z1", "z2", "lt1"}:
                effort_mismatch = "on_target"
            else:
                effort_mismatch = "unknown"

        elif planned_intent == "quality":
            # Quality runs should be in higher zones (lt2, threshold, tempo, vo2max)
            if hr_zone in {"z1", "z2"}:
                effort_mismatch = "too_easy"
                recommendation = "Workout may not be providing intended stimulus."
            elif hr_zone in {"lt2", "threshold", "tempo", "vo2max"}:
                effort_mismatch = "on_target"
            else:
                effort_mismatch = "unknown"

        elif planned_intent == "long":
            # Long runs allow steady/MP drift and split-intent (easy + MP finish)
            # Acceptable zones: z1, z2, lt1, lt2 (steady/MP equivalent)
            # Only flag as too_hard if it goes into threshold/tempo/vo2max
            if hr_zone in {"threshold", "tempo", "vo2max"}:
                effort_mismatch = "too_hard"
                recommendation = "Long run pace appears too fast; consider slowing to maintain endurance focus."
            elif hr_zone in {"z1", "z2", "lt1", "lt2"}:
                # Long runs can drift into lt2 (steady/MP zones) - this is acceptable
                # Split-intent long runs (easy + MP finish) are common and valid
                effort_mismatch = "on_target"
            else:
                effort_mismatch = "unknown"

        elif planned_intent == "rest":
            # Rest days should have no activity or very low HR
            if hr_zone in {"z2", "lt1", "lt2", "threshold", "tempo", "vo2max"}:
                effort_mismatch = "too_hard"
                recommendation = "Rest day appears to have significant activity; ensure adequate recovery."
            else:
                effort_mismatch = "on_target"

    # If no HR data, cannot determine mismatch
    if hr_zone is None or hr_zone == "unknown":
        effort_mismatch = "unknown"

    return ReconciliationResult(
        planned_intent=planned_intent,
        planned_pace=planned_pace,
        observed_pace=observed_pace,
        hr_zone=hr_zone,
        effort_mismatch=effort_mismatch,
        recommendation=recommendation,
    )
