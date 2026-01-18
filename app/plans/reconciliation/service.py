"""Reconciliation service for wiring reconciliation into activity ingestion.

This module provides the service layer that:
1. Extracts executed workout data from Activity
2. Retrieves planned session and athlete data
3. Calls reconcile_workout
4. Persists reconciliation results
"""

import contextlib

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.athletes.models import AthletePaceProfile
from app.db.models import Activity, Athlete, AthleteProfile, PlannedSession, StravaAccount, WorkoutReconciliation
from app.pairing.session_links import SessionLink
from app.plans.reconciliation.reconcile import reconcile_workout
from app.plans.reconciliation.types import ExecutedWorkout


def extract_hr_from_activity(activity: Activity) -> tuple[int | None, int | None]:
    """Extract HR data from activity raw_json.

    Args:
        activity: Activity with raw_json containing HR data

    Returns:
        Tuple of (avg_hr, max_hr) or (None, None) if not available
    """
    if not activity.raw_json or not isinstance(activity.raw_json, dict):
        return (None, None)

    avg_hr = activity.raw_json.get("average_heartrate")
    max_hr = activity.raw_json.get("max_heartrate")

    # Convert to int if available
    avg_hr_int = None
    if avg_hr is not None:
        with contextlib.suppress(ValueError, TypeError):
            avg_hr_int = int(float(avg_hr))

    max_hr_int = None
    if max_hr is not None:
        with contextlib.suppress(ValueError, TypeError):
            max_hr_int = int(float(max_hr))

    return (avg_hr_int, max_hr_int)


def calculate_pace_from_activity(activity: Activity) -> float | None:
    """Calculate pace from activity distance and duration.

    Args:
        activity: Activity with distance and duration

    Returns:
        Pace in minutes per mile, or None if cannot calculate
    """
    if not activity.distance_meters or not activity.duration_seconds:
        return None

    # Convert meters to miles
    distance_miles = activity.distance_meters / 1609.34
    # Convert seconds to minutes
    duration_minutes = activity.duration_seconds / 60.0

    if distance_miles <= 0:
        return None

    return duration_minutes / distance_miles


def get_athlete_pace_profile(_session: Session, _user_id: str) -> AthletePaceProfile | None:
    """Get athlete pace profile from user settings.

    For now, this is a placeholder. In the future, this should retrieve
    the pace profile from UserSettings or a dedicated pace profile table.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        AthletePaceProfile if available, None otherwise
    """
    # TODO: Implement retrieval from UserSettings or pace profile table
    # For now, return None to allow reconciliation to work without HR zones
    return None


def reconcile_activity_if_paired(
    session: Session,
    activity: Activity,
) -> WorkoutReconciliation | None:
    """Reconcile activity if it's paired with a planned session.

    This is the main entry point for reconciliation from activity ingestion.
    It's called after an activity is paired with a planned session.

    Args:
        session: Database session
        activity: Activity that may be paired with a planned session

    Returns:
        WorkoutReconciliation if reconciliation was performed, None otherwise
    """
    # Schema v2: Check for pairing via session_links table instead of activity.planned_session_id
    # Get session link for this activity
    link = session.execute(
        select(SessionLink).where(
            SessionLink.activity_id == activity.id,
            SessionLink.status.in_(["proposed", "confirmed"]),  # Only active links
        )
    ).scalar_one_or_none()

    if not link or not link.planned_session_id:
        return None

    # Get planned session
    planned_session = session.execute(
        select(PlannedSession).where(PlannedSession.id == link.planned_session_id)
    ).scalar_one_or_none()

    if not planned_session:
        logger.warning(
            f"Planned session {activity.planned_session_id} not found for activity {activity.id}"
        )
        return None

    # Extract executed workout data
    avg_hr, max_hr = extract_hr_from_activity(activity)
    distance_miles = None
    if activity.distance_meters:
        distance_miles = activity.distance_meters / 1609.34
    duration_min = None
    if activity.duration_seconds:
        duration_min = int(activity.duration_seconds / 60)
    pace = calculate_pace_from_activity(activity)

    executed = ExecutedWorkout(
        planned_session_id=planned_session.id,
        actual_distance_miles=distance_miles,
        actual_duration_min=duration_min,
        avg_hr=avg_hr,
        max_hr=max_hr,
        avg_pace_min_per_mile=pace,
    )

    # Get athlete pace profile (optional - reconciliation works without it)
    athlete_pace_profile = get_athlete_pace_profile(session, activity.user_id)

    # Perform reconciliation
    try:
        result = reconcile_workout(
            planned_session=planned_session,
            executed=executed,
            athlete_pace_profile=athlete_pace_profile,
        )
    except Exception as e:
        logger.error(f"Reconciliation failed for activity {activity.id}: {e}")
        return None

    # Get athlete_id as int (from AthleteProfile or StravaAccount)
    athlete_id_int = 0
    try:
        profile = session.execute(
            select(AthleteProfile).where(AthleteProfile.user_id == activity.user_id)
        ).scalar_one_or_none()
        if profile:
            athlete_id_int = profile.athlete_id
        else:
            account = session.execute(
                select(StravaAccount).where(StravaAccount.user_id == activity.user_id)
            ).scalar_one_or_none()
            if account:
                with contextlib.suppress(ValueError, TypeError):
                    athlete_id_int = int(account.athlete_id)
    except Exception as e:
        logger.warning(f"Could not get athlete_id for reconciliation: {e}")

    # Persist reconciliation result
    reconciliation = WorkoutReconciliation(
        planned_session_id=planned_session.id,
        user_id=activity.user_id,
        athlete_id=athlete_id_int,
        effort_mismatch=result.effort_mismatch,
        hr_zone=result.hr_zone,
        recommendation=result.recommendation,
    )
    session.add(reconciliation)
    session.flush()

    logger.info(
        f"Reconciliation completed for activity {activity.id} -> planned session {planned_session.id}: "
        f"mismatch={result.effort_mismatch}, hr_zone={result.hr_zone}"
    )

    return reconciliation
