"""B7 — Calendar Persistence (Idempotent, Safe, Deterministic).

This module persists fully validated, text-complete plans into the calendar system.
Input is FINAL. No mutation. No regeneration. No retries that change content.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import PlannedSession as DBPlannedSession
from app.db.schema_v2_map import (
    combine_date_time,
    km_to_meters,
    mi_to_meters,
    minutes_to_seconds,
    normalize_sport,
)
from app.domains.training_plan.enums import DayType, WeekFocus
from app.domains.training_plan.models import PlanContext, PlannedSession, PlannedWeek, SessionTextOutput
from app.plans.week_planner import assign_intent_from_day_type


@dataclass
class PersistResult:
    """Result of plan persistence operation.

    Attributes:
        plan_id: Unique plan identifier
        created: Number of sessions created
        updated: Number of sessions updated
        skipped: Number of sessions skipped
        warnings: List of warning messages
    """

    plan_id: str
    created: int
    updated: int
    skipped: int
    warnings: list[str]


def _generate_plan_id() -> str:
    """Generate a unique plan ID.

    Returns:
        UUID string for plan identification
    """
    return str(uuid.uuid4())


def _compute_plan_start_date(ctx: PlanContext) -> date:
    """Compute plan start date from context.

    For race plans: start_date = target_date - weeks
    For season plans: start_date = today (Monday of current week)

    Args:
        ctx: Plan context

    Returns:
        Start date (Monday of first week)
    """
    if ctx.target_date:
        # Race plan: work backwards from target date
        target = date.fromisoformat(ctx.target_date)
        # Start date is (weeks) weeks before target, on a Monday
        # Calculate Monday of the week that is (weeks) weeks before target
        weeks_before = ctx.weeks
        approximate_start = target - timedelta(weeks=weeks_before)
        # Find Monday of that week
        days_since_monday = approximate_start.weekday()
        return approximate_start - timedelta(days=days_since_monday)

    # Season plan: start from Monday of current week

    today = datetime.now(tz=UTC).date()
    days_since_monday = today.weekday()
    return today - timedelta(days=days_since_monday)


def _compute_session_date(plan_start: date, week_index: int, day_index: int) -> date:
    """Compute calendar date for a session.

    Args:
        plan_start: Monday of first week
        week_index: Week number (1-based)
        day_index: Day index (0=Monday, 6=Sunday)

    Returns:
        Calendar date for the session
    """
    weeks_offset = week_index - 1
    days_offset = weeks_offset * 7 + day_index
    return plan_start + timedelta(days=days_offset)


def _get_time_default(session: PlannedSession) -> str | None:
    """Get default time-of-day for session type.

    Args:
        session: Planned session

    Returns:
        Time string (HH:MM) or None for rest days
    """
    if session.day_type == DayType.REST:
        return None

    # Map day_type to time defaults
    time_map: dict[DayType, str] = {
        DayType.EASY: "07:00",
        DayType.LONG: "08:00",
        DayType.QUALITY: "06:00",  # threshold/vo2
        DayType.RACE: "06:00",
        DayType.CROSS: "07:00",
    }
    return time_map.get(session.day_type, "07:00")


def _determine_phase(focus: WeekFocus) -> str:
    """Determine training phase from week focus.

    Args:
        focus: Week focus

    Returns:
        Phase string: "build" or "taper"
    """
    taper_focuses = {WeekFocus.TAPER, WeekFocus.SHARPENING}
    return "taper" if focus in taper_focuses else "build"


def _map_session_type(day_type: DayType) -> str:
    """Map day_type to session_type string.

    Args:
        day_type: Day type enum

    Returns:
        Session type string (easy, threshold, long, etc.) - legacy/auxiliary field
    """
    mapping: dict[DayType, str] = {
        DayType.EASY: "easy",
        DayType.QUALITY: "threshold",  # Default for quality days
        DayType.LONG: "long",
        DayType.RACE: "race",
        DayType.REST: "rest",
        DayType.CROSS: "cross",
    }
    return mapping.get(day_type, "easy")


def _map_intent_from_day_type(day_type: DayType) -> str:
    """Map day_type to workout intent (authoritative field).

    Args:
        day_type: DayType enum

    Returns:
        Workout intent string (rest, easy, long, quality)
    """
    # Map DayType enum values to intent
    if day_type == DayType.REST:
        return "rest"
    if day_type == DayType.LONG:
        return "long"
    if day_type == DayType.QUALITY:
        return "quality"
    # Default to easy for EASY, CROSS, RACE, etc.
    return "easy"


def _extract_distance_mi(session: PlannedSession, text_output: SessionTextOutput | None) -> float | None:
    """Extract distance in miles from session.

    Args:
        session: Planned session
        text_output: Optional text output with computed metrics

    Returns:
        Distance in miles or None
    """
    if text_output and "total_distance_mi" in text_output.computed:
        computed_dist = text_output.computed["total_distance_mi"]
        if isinstance(computed_dist, (int, float)):
            return float(computed_dist)

    # Fallback: use session distance (assumed to be in miles if from planner)
    # The planner uses unit-agnostic distance, but we'll assume miles for now
    # TODO: Verify unit conversion if needed
    return float(session.distance) if session.distance > 0 else None


def _extract_duration_min(_session: PlannedSession, text_output: SessionTextOutput | None) -> int | None:
    """Extract duration in minutes from session (for conversion to seconds).

    Args:
        session: Planned session
        text_output: Optional text output with computed metrics

    Returns:
        Duration in minutes or None
    """
    if text_output and "total_duration_min" in text_output.computed:
        computed_dur = text_output.computed["total_duration_min"]
        if isinstance(computed_dur, (int, float)):
            return int(computed_dur)

    # Check intensity_minutes as fallback
    if text_output and "intensity_minutes" in text_output.computed:
        intensity = text_output.computed["intensity_minutes"]
        if isinstance(intensity, dict) and "total" in intensity:
            total = intensity["total"]
            if isinstance(total, int):
                return total

    return None


def _convert_distance_to_meters(distance_mi: float | None) -> float | None:
    """Convert distance from miles to meters (schema v2).

    Args:
        distance_mi: Distance in miles

    Returns:
        Distance in meters or None
    """
    if distance_mi is None:
        return None
    return mi_to_meters(distance_mi)


def _get_tags(session: PlannedSession) -> list[str]:
    """Extract tags from session template.

    Args:
        session: Planned session

    Returns:
        List of tag strings
    """
    return list(session.template.tags) if session.template.tags else []


def _persist_week_sessions(
    db_session: Session,
    week: PlannedWeek,
    ctx: PlanContext,
    plan_start: date,
    *,
    plan_id: str,
    user_id: str,
) -> tuple[int, int, int, list[str]]:
    """Persist all sessions for a single week.

    Args:
        db_session: Database session
        week: Planned week with sessions
        ctx: Plan context
        plan_start: Plan start date
        plan_id: Plan ID
        user_id: User ID

    Returns:
        Tuple of (created_count, updated_count, skipped_count, warnings_list)
    """
    # Group sessions by day_index for session_order
    sessions_by_day: dict[int, list[PlannedSession]] = {}
    for session in week.sessions:
        day_idx = session.day_index
        if day_idx not in sessions_by_day:
            sessions_by_day[day_idx] = []
        sessions_by_day[day_idx].append(session)

    # Sort sessions within each day (by day_type or template)
    for day_sessions in sessions_by_day.values():
        day_sessions.sort(key=lambda s: (s.day_type.value, s.template.template_id))

    week_created = week_updated = week_skipped = 0
    week_warnings: list[str] = []

    # Persist sessions for this week
    for day_idx, day_sessions in sessions_by_day.items():
        for session_order, session in enumerate(day_sessions):
            try:
                result = _upsert_session(
                    db_session=db_session,
                    ctx=ctx,
                    planned_session=session,
                    week=week,
                    plan_start=plan_start,
                    plan_id=plan_id,
                    user_id=user_id,
                    session_order=session_order,
                )

                if result == "created":
                    week_created += 1
                elif result == "updated":
                    week_updated += 1
                else:
                    week_skipped += 1

            except IntegrityError as e:
                # Handle unique constraint violations
                error_msg = str(e).lower()
                if "unique" in error_msg or "duplicate" in error_msg:
                    week_warnings.append(
                        f"Week {week.week_index}, day {day_idx}: "
                        f"Duplicate session detected (may be concurrent update): {e}"
                    )
                    week_skipped += 1
                else:
                    raise

            except Exception as e:
                error_msg = (
                    f"B7: Failed to persist session "
                    f"(week_index={week.week_index}, day_index={day_idx}, "
                    f"session_order={session_order}, "
                    f"error_type={type(e).__name__})"
                )
                logger.error(error_msg)
                week_warnings.append(f"Week {week.week_index}, day {day_idx}: Failed to persist: {e}")
                week_skipped += 1

    return week_created, week_updated, week_skipped, week_warnings


def _upsert_session(
    db_session: Session,
    ctx: PlanContext,
    planned_session: PlannedSession,
    *,
    week: PlannedWeek,
    plan_start: date,
    plan_id: str,
    user_id: str,
    session_order: int,
) -> str:
    """Upsert a single session into the database.

    Args:
        db_session: Database session
        ctx: Plan context
        planned_session: Planned session to persist
        week: Planned week containing the session
        plan_start: Plan start date (Monday of first week)
        plan_id: Plan identifier
        user_id: User ID
        session_order: Order of session within the day (0-based)

    Returns:
        Result string: "created", "updated", or "skipped"
    """
    # Compute calendar date
    session_date = _compute_session_date(plan_start, week.week_index, planned_session.day_index)

    # Extract data from planned session
    text_output = planned_session.text_output
    title = text_output.title if text_output else f"{planned_session.day_type.value.title()} Run"
    description = text_output.description if text_output else ""
    distance_mi = _extract_distance_mi(planned_session, text_output)
    distance_meters = _convert_distance_to_meters(distance_mi)  # Schema v2: meters
    duration_min = _extract_duration_min(planned_session, text_output)
    duration_seconds = minutes_to_seconds(duration_min)  # Schema v2: seconds
    time_str = _get_time_default(planned_session)
    phase = _determine_phase(week.focus)
    session_type = _map_session_type(planned_session.day_type)
    intent = _map_intent_from_day_type(planned_session.day_type)  # Authoritative field
    tags = _get_tags(planned_session)
    philosophy_id = ctx.philosophy.philosophy_id if ctx.philosophy else None
    template_id = planned_session.template.template_id

    # Schema v2: Combine date + time into starts_at (TIMESTAMPTZ)
    starts_at = combine_date_time(session_date, time_str)

    # Schema v2: Normalize sport type
    sport = normalize_sport("run")  # Default to "run", can be extended later

    # Build unique constraint key for lookup (schema v2 dedupe key)
    # Note: We use title and sport for additional uniqueness beyond just starts_at
    try:
        # Build query conditions (schema v2: no athlete_id, use starts_at, season_plan_id)
        conditions = [
            DBPlannedSession.user_id == user_id,
            DBPlannedSession.starts_at == starts_at,
            DBPlannedSession.title == title,
            DBPlannedSession.sport == sport,
        ]

        # Handle season_plan_id (can be None for some plans)
        # Schema v2: plan_id -> season_plan_id
        if plan_id is not None:
            conditions.append(DBPlannedSession.season_plan_id == plan_id)
        else:
            conditions.append(DBPlannedSession.season_plan_id.is_(None))

        query = select(DBPlannedSession).where(and_(*conditions))
        existing = db_session.execute(query).scalar_one_or_none()
    except Exception as e:
        starts_at_str = starts_at.isoformat() if starts_at else None
        error_msg = (
            f"B7: Failed to persist session "
            f"(user_id={user_id}, "
            f"season_plan_id={plan_id}, starts_at={starts_at_str}, "
            f"error_type={type(e).__name__})"
        )
        logger.error(
            error_msg,
            week_index=week.week_index,
            day_index=planned_session.day_index,
            session_order=session_order,
        )
        raise

    if existing:
        # Update existing session (schema v2 fields)
        db_session_obj = existing
        db_session_obj.title = title
        db_session_obj.notes = description
        db_session_obj.distance_meters = distance_meters  # Schema v2: distance_meters
        db_session_obj.duration_seconds = duration_seconds  # Schema v2: duration_seconds
        db_session_obj.starts_at = starts_at  # Schema v2: starts_at (ensure time is updated if changed)
        db_session_obj.sport = sport  # Schema v2: sport
        db_session_obj.intensity = session_type
        db_session_obj.session_type = session_type
        db_session_obj.intent = intent  # Authoritative field
        db_session_obj.tags = tags if tags else []  # Schema v2: tags is list
        db_session_obj.phase = phase
        db_session_obj.philosophy_id = philosophy_id
        db_session_obj.template_id = template_id
        db_session_obj.week_number = week.week_index
        # Schema v2: season_plan_id already matched in query, no need to update

        return "updated"

    # Create new session (schema v2)
    try:
        db_session_obj = DBPlannedSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            starts_at=starts_at,  # Schema v2: starts_at instead of date + time
            sport=sport,  # Schema v2: sport instead of type
            title=title,
            duration_seconds=duration_seconds,  # Schema v2: duration_seconds instead of duration_minutes
            distance_meters=distance_meters,  # Schema v2: distance_meters instead of distance_km/distance_mi
            intensity=session_type,
            session_type=session_type,
            intent=intent,  # Authoritative field
            notes=description,
            season_plan_id=plan_id,  # Schema v2: season_plan_id instead of plan_id
            week_number=week.week_index,
            session_order=session_order,
            phase=phase,
            source="planner_v2",
            philosophy_id=philosophy_id,
            template_id=template_id,
            tags=tags if tags else [],  # Schema v2: tags is list, not None
            status="planned",  # Schema v2: status field
            # Schema v2: removed athlete_id, plan_type, completed
        )

        db_session.add(db_session_obj)
    except Exception as e:
        error_msg = (
            f"B7: Failed to create new session object "
            f"(week_index={week.week_index}, "
            f"day_index={planned_session.day_index}, "
            f"session_order={session_order}, "
            f"error_type={type(e).__name__})"
        )
        logger.error(error_msg)
        raise

    return "created"


def persist_plan(
    ctx: PlanContext,
    weeks: list[PlannedWeek],
    user_id: str,
    athlete_id: int,
    plan_id: str | None = None,
) -> PersistResult:
    """Persist fully validated plan to calendar system.

    This function:
    - Computes calendar dates from week_index and day_index
    - Persists sessions week-by-week in transactions
    - Handles idempotency via unique constraints
    - Returns detailed result with counts

    Args:
        ctx: Plan context with philosophy and metadata
        weeks: List of planned weeks (ordered by week_index)
        user_id: User ID
        athlete_id: Athlete ID
        plan_id: Optional plan ID (generated if not provided)

    Returns:
        PersistResult with creation/update counts and warnings

    Raises:
        ValueError: If ctx.philosophy is None (philosophy must be selected before B7)
    """
    if ctx.philosophy is None:
        raise ValueError("PlanContext.philosophy must be set before B7 (philosophy selection required)")

    if not plan_id:
        plan_id = _generate_plan_id()

    logger.info(
        "B7: Starting calendar persistence",
        plan_id=plan_id,
        user_id=user_id,
        athlete_id=athlete_id,
        week_count=len(weeks),
    )

    created = updated = skipped = 0
    warnings: list[str] = []

    # Compute plan start date
    plan_start = _compute_plan_start_date(ctx)

    # Import here to avoid circular imports
    from app.db.session import get_session  # noqa: PLC0415

    # Persist week-by-week
    for week in weeks:
        try:
            with get_session() as db_session:
                week_created, week_updated, week_skipped, week_warnings = _persist_week_sessions(
                    db_session=db_session,
                    week=week,
                    ctx=ctx,
                    plan_start=plan_start,
                    plan_id=plan_id,
                    user_id=user_id,
                )
                created += week_created
                updated += week_updated
                skipped += week_skipped
                warnings.extend(week_warnings)
                db_session.commit()

        except Exception as e:
            # Week-level rollback (transaction already rolled back by context manager)
            error_msg = f"Week {week.week_index} failed: {e}"
            warnings.append(error_msg)
            logger.error(
                "B7: Week persistence failed",
                week_index=week.week_index,
                error=str(e),
            )
            # Continue with other weeks

    logger.info(
        "B7: Calendar persistence complete",
        plan_id=plan_id,
        created=created,
        updated=updated,
        skipped=skipped,
        warning_count=len(warnings),
    )

    return PersistResult(
        plan_id=plan_id,
        created=created,
        updated=updated,
        skipped=skipped,
        warnings=warnings,
    )
