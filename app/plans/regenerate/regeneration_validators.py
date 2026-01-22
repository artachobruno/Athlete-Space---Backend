"""Validators for plan regeneration.

Hard safety layer that enforces invariants before regeneration.
"""

from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AthleteProfile, PlannedSession
from app.plans.race.utils import is_race_week
from app.plans.regenerate.types import RegenerationRequest


def validate_regeneration(
    req: RegenerationRequest,
    athlete_profile: AthleteProfile,
    today: date,
    session: Session,
    user_id: str,
    _athlete_id: int,  # Unused: kept for API compatibility
) -> None:
    """Validate regeneration request against safety rules.

    Rules:
    1. start_date >= today (no past regeneration)
    2. Block regeneration starting inside race week (unless allow_race_week)
    3. Block regeneration if no future sessions exist
    4. Enforce end_date >= start_date

    Args:
        req: Regeneration request
        athlete_profile: Athlete profile (for race date)
        today: Today's date
        session: Database session
        user_id: User ID
        athlete_id: Athlete ID

    Raises:
        ValueError: If validation fails
    """
    # Rule 1: start_date >= today
    if req.start_date < today:
        raise ValueError(
            f"Regeneration start_date ({req.start_date}) must be >= today ({today}). "
            "Cannot regenerate past sessions."
        )

    # Rule 4: end_date >= start_date (if provided)
    if req.end_date is not None and req.end_date < req.start_date:
        raise ValueError(
            f"Regeneration end_date ({req.end_date}) must be >= start_date ({req.start_date})"
        )

    # Rule 2: Block regeneration starting inside race week (unless allow_race_week)
    race_date = athlete_profile.race_date
    if race_date is not None:
        # Normalize race_date to date (handle both datetime and date types)
        race_date_normalized = race_date.date() if hasattr(race_date, "date") else race_date

        # Calculate week boundaries for start_date
        days_since_monday = req.start_date.weekday()
        week_start = req.start_date - timedelta(days=days_since_monday)
        week_end = week_start + timedelta(days=6)

        if is_race_week(week_start, week_end, race_date_normalized) and not req.allow_race_week:
            raise ValueError(
                f"Regeneration starting in race week ({week_start} to {week_end}) "
                "is blocked. Set allow_race_week=True to override."
            )

    # Rule 3: Block regeneration if no future sessions exist
    start_datetime = datetime.combine(req.start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_datetime = None
    if req.end_date:
        end_datetime = datetime.combine(req.end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    query = select(PlannedSession).where(
        PlannedSession.user_id == user_id,
        PlannedSession.starts_at >= start_datetime,
    )

    if end_datetime:
        query = query.where(PlannedSession.starts_at <= end_datetime)

    # Exclude completed/deleted/skipped sessions
    query = query.where(
        PlannedSession.status.notin_(["completed", "deleted", "skipped"]),
    )

    existing_sessions = list(session.execute(query).scalars().all())

    if not existing_sessions:
        raise ValueError(
            f"No future sessions found in range {req.start_date} to {req.end_date or 'plan end'}. "
            "Cannot regenerate empty range."
        )

    logger.info(
        "Regeneration validation passed",
        start_date=req.start_date.isoformat(),
        end_date=req.end_date.isoformat() if req.end_date else None,
        existing_sessions_count=len(existing_sessions),
    )
