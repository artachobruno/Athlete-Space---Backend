"""B7 — Calendar Persistence (Idempotent, Safe, Deterministic).

This module persists fully validated, text-complete plans into the calendar system.
Input is FINAL. No mutation. No regeneration. No retries that change content.
"""

import asyncio
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import NoReturn

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import PlannedSession as DBPlannedSession
from app.db.models import StravaAccount
from app.db.schema_v2_map import (
    combine_date_time,
    km_to_meters,
    mi_to_meters,
    minutes_to_seconds,
    normalize_sport,
)
from app.db.session import get_session
from app.domains.training_plan.enums import DayType, PlanType, WeekFocus
from app.domains.training_plan.models import PlanContext, PlannedSession, PlannedWeek, SessionTextOutput
from app.plans.week_planner import assign_intent_from_day_type
from app.services.intelligence.scheduler import trigger_daily_decision_for_user


@dataclass
class PersistResult:
    """Result of plan persistence operation.

    success=True iff at least one session was created or updated.
    session_ids contains IDs of created/updated rows only; skipped rows are excluded.

    Attributes:
        plan_id: Unique plan identifier
        created: Number of sessions created
        updated: Number of sessions updated
        skipped: Number of sessions skipped
        warnings: List of warning messages
        success: True iff created + updated > 0
        session_ids: IDs of created/updated sessions (UUID strings)
    """

    plan_id: str
    created: int
    updated: int
    skipped: int
    warnings: list[str]
    success: bool
    session_ids: list[str]


def _generate_plan_id() -> str:
    """Generate a unique plan ID.

    Returns:
        UUID string for plan identification
    """
    return str(uuid.uuid4())


def _compute_plan_start_date(ctx: PlanContext) -> date:
    """Compute plan start date from context.

    For race plans: start_date ensures the last week includes the race date.
    The plan spans exactly ctx.weeks weeks, with week ctx.weeks containing the race date.
    For season plans: start_date = today (Monday of current week)

    Args:
        ctx: Plan context

    Returns:
        Start date (Monday of first week)
    """
    if ctx.target_date:
        # Race plan: ensure the last week includes the race date
        target = date.fromisoformat(ctx.target_date)
        # Find the Monday of the week containing the race date (this is week ctx.weeks)
        days_since_monday = target.weekday()
        race_week_monday = target - timedelta(days=days_since_monday)
        # Go back (weeks - 1) weeks from race week Monday to get start date
        # This ensures week 1 starts (weeks - 1) weeks before race week
        weeks_before = ctx.weeks - 1
        return race_week_monday - timedelta(weeks=weeks_before)

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
) -> tuple[int, int, int, list[str], list[str]]:
    """Persist all sessions for a single week.

    Args:
        db_session: Database session
        week: Planned week with sessions
        ctx: Plan context
        plan_start: Plan start date
        plan_id: Plan ID
        user_id: User ID

    Returns:
        Tuple of (created_count, updated_count, skipped_count, warnings_list, session_ids)
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
    week_session_ids: list[str] = []

    # Persist sessions for this week
    for day_idx, day_sessions in sessions_by_day.items():
        for session_order, session in enumerate(day_sessions):
            try:
                # Extract session info for logging before upsert
                text_output = session.text_output
                title = text_output.title if text_output else f"{session.day_type.value.title()} Run"
                description = text_output.description if text_output else ""
                is_running = session.day_type != DayType.REST

                outcome, session_id = _upsert_session(
                    db_session=db_session,
                    ctx=ctx,
                    planned_session=session,
                    week=week,
                    plan_start=plan_start,
                    _plan_id=plan_id,
                    user_id=user_id,
                    session_order=session_order,
                )

                if outcome == "created":
                    week_created += 1
                    if session_id:
                        week_session_ids.append(session_id)
                    # Log description for running sessions
                    if is_running:
                        logger.info(
                            f"Running session created: {title} - {description}",
                            session_id=session_id,
                            title=title,
                            description=description,
                            day_index=day_idx,
                            week_index=week.week_index,
                            session_order=session_order,
                            day_type=session.day_type.value,
                            distance_mi=session.distance,
                            user_id=user_id,
                        )
                elif outcome == "updated":
                    week_updated += 1
                    if session_id:
                        week_session_ids.append(session_id)
                    # Log description for running sessions that were updated
                    if is_running:
                        logger.info(
                            f"Running session updated: {title} - {description}",
                            session_id=session_id,
                            title=title,
                            description=description,
                            day_index=day_idx,
                            week_index=week.week_index,
                            session_order=session_order,
                            day_type=session.day_type.value,
                            distance_mi=session.distance,
                            user_id=user_id,
                        )
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
                raise RuntimeError(f"Week {week.week_index}, day {day_idx}: Failed to persist: {e}") from e

    return week_created, week_updated, week_skipped, week_warnings, week_session_ids


def _upsert_session(
    db_session: Session,
    ctx: PlanContext,
    planned_session: PlannedSession,
    *,
    week: PlannedWeek,
    plan_start: date,
    _plan_id: str,  # Kept for API compatibility, not currently used
    user_id: str,
    session_order: int,
) -> tuple[str, str | None]:
    """Upsert a single session into the database.

    Args:
        db_session: Database session
        ctx: Plan context
        planned_session: Planned session to persist
        week: Planned week containing the session
        plan_start: Plan start date (Monday of first week)
        _plan_id: Plan identifier (kept for API compatibility)
        user_id: User ID
        session_order: Order of session within the day (0-based)

    Returns:
        Tuple of (outcome "created"|"updated"|"skipped", session id or None)
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
    session_type = _map_session_type(planned_session.day_type)
    intent = _map_intent_from_day_type(planned_session.day_type)  # Authoritative field
    tags = _get_tags(planned_session)

    # Schema v2: Combine date + time into starts_at (TIMESTAMPTZ)
    starts_at = combine_date_time(session_date, time_str)

    # Schema v2: Normalize sport type
    sport = normalize_sport("run")  # Default to "run", can be extended later

    # Determine season_plan_id based on plan type
    # Race plans don't have season_plan records, so season_plan_id should be None
    # Season plans could have season_plan records, but we're not creating them yet
    # For now, set season_plan_id to None for all plans to avoid FK violations
    # TODO: Create season_plan records for season plans and link them properly
    season_plan_id: str | None = None
    if ctx.plan_type == PlanType.SEASON:
        # For season plans, we could create a season_plan record here
        # For now, set to None to avoid FK violations
        # In the future, create SeasonPlan record and use its ID
        season_plan_id = None
    # For race plans (PlanType.RACE), season_plan_id is always None

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
        # Schema v2: Only set season_plan_id if it's a valid foreign key
        if season_plan_id is not None:
            conditions.append(DBPlannedSession.season_plan_id == season_plan_id)
        else:
            conditions.append(DBPlannedSession.season_plan_id.is_(None))

        query = select(DBPlannedSession).where(and_(*conditions))
        existing = db_session.execute(query).scalar_one_or_none()
    except Exception as e:
        starts_at_str = starts_at.isoformat() if starts_at else None
        error_msg = (
            f"B7: Failed to persist session "
            f"(user_id={user_id}, "
            f"season_plan_id={season_plan_id}, starts_at={starts_at_str}, "
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
        # Schema v2: season_plan_id already matched in query, no need to update
        # Note: week_number, session_order, phase, source, philosophy_id, template_id
        # are not part of the PlannedSession model schema v2

        return ("updated", db_session_obj.id)

    # Create new session (schema v2)
    try:
        # Validate starts_at is not None (required field)
        if starts_at is None:
            error_msg = (
                f"starts_at cannot be None (week_index={week.week_index}, "
                f"day_index={planned_session.day_index}, session_date={session_date}, time_str={time_str})"
            )
            raise ValueError(error_msg)  # noqa: TRY301

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
            season_plan_id=season_plan_id,  # Schema v2: season_plan_id (None for race plans, None for season plans until we create records)
            tags=tags if tags else [],  # Schema v2: tags is list, not None
            status="planned",  # Schema v2: status field
            # Schema v2: removed athlete_id, plan_type, completed
            # Note: week_number, session_order, phase, source, philosophy_id, template_id
            # are not part of the PlannedSession model schema v2
        )

        db_session.add(db_session_obj)
    except Exception as e:
        error_msg = (
            f"B7: Failed to create new session object "
            f"(week_index={week.week_index}, "
            f"day_index={planned_session.day_index}, "
            f"session_order={session_order}, "
            f"error_type={type(e).__name__}, "
            f"error={e!s})"
        )
        logger.error(error_msg)
        raise

    return ("created", db_session_obj.id)


def _ensure_race_session(
    db_session: Session,
    race_date: date,
    plan_start: date,
    ctx: PlanContext,
    user_id: str,
    weeks: list[PlannedWeek],
) -> str | None:
    """Ensure a race session exists on the race date.

    Checks if a race session already exists on the race date. If not, creates one.
    This ensures race plans always include the race itself as a planned session.

    Args:
        db_session: Database session
        race_date: Race date
        plan_start: Plan start date (Monday of first week)
        ctx: Plan context
        user_id: User ID
        weeks: List of planned weeks (to check if race is already in plan)

    Returns:
        Session ID if race session was created, None if it already exists
    """
    # Check if race date falls within any week in the plan
    for week in weeks:
        week_start = _compute_session_date(plan_start, week.week_index, 0)  # Monday of this week
        week_end = week_start + timedelta(days=6)  # Sunday of this week
        if week_start <= race_date <= week_end:
            # Check if there's already a race session in this week on the race date
            for session in week.sessions:
                session_date = _compute_session_date(plan_start, week.week_index, session.day_index)
                if session_date == race_date and session.day_type == DayType.RACE:
                    # Race session already exists in the plan
                    logger.debug(
                        "Race session already exists in plan",
                        race_date=race_date.isoformat(),
                        week_index=week.week_index,
                        day_index=session.day_index,
                    )
                    return None
            break

    # Check if race session already exists in database on race date
    # Check for any session on race date with session_type="race"
    race_date_start = datetime.combine(race_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    race_date_end = datetime.combine(race_date, datetime.max.time()).replace(tzinfo=timezone.utc)
    sport = normalize_sport("run")

    existing_query = select(DBPlannedSession).where(
        and_(
            DBPlannedSession.user_id == user_id,
            DBPlannedSession.starts_at >= race_date_start,
            DBPlannedSession.starts_at <= race_date_end,
            DBPlannedSession.sport == sport,
            DBPlannedSession.session_type == "race",
        )
    )
    existing = db_session.execute(existing_query).scalar_one_or_none()

    if existing:
        logger.debug(
            "Race session already exists in database",
            race_date=race_date.isoformat(),
            session_id=existing.id,
        )
        return None

    # Create race session
    race_distance_str = ctx.race_distance.value if ctx.race_distance else "Race"
    race_title = f"{race_distance_str} Race"
    race_starts_at = combine_date_time(race_date, "06:00")  # Default race time

    try:
        race_session = DBPlannedSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            starts_at=race_starts_at,
            sport=sport,
            title=race_title,
            session_type="race",
            intensity="race",
            intent="race",
            notes=f"Race day: {race_distance_str}",
            season_plan_id=None,  # Race plans don't have season_plan_id
            tags=["race"],
            status="planned",
            distance_meters=None,  # Race distance not known in advance
            duration_seconds=None,  # Race duration not known in advance
        )

        db_session.add(race_session)
        logger.info(
            "Created race session on race date",
            race_date=race_date.isoformat(),
            session_id=race_session.id,
            title=race_title,
        )
    except Exception as e:
        logger.error(
            "Failed to create race session",
            race_date=race_date.isoformat(),
            error=str(e),
            error_type=type(e).__name__,
        )
        # Don't raise - this is best-effort, plan can still succeed without explicit race session
        return None
    else:
        return race_session.id


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

    # Calculate total sessions to persist
    total_sessions = sum(len(week.sessions) for week in weeks)

    sessions_per_week = [len(week.sessions) for week in weeks]
    logger.info(
        f"B7: Starting calendar persistence - {total_sessions} sessions across {len(weeks)} weeks "
        f"(sessions per week: {sessions_per_week})",
        plan_id=plan_id,
        user_id=user_id,
        athlete_id=athlete_id,
        week_count=len(weeks),
        total_sessions_to_persist=total_sessions,
        sessions_per_week=sessions_per_week,
    )

    created = updated = skipped = 0
    warnings: list[str] = []
    session_ids: list[str] = []

    # Compute plan start date
    plan_start = _compute_plan_start_date(ctx)

    # Single transaction: either all weeks persist or none. No partial writes.
    with get_session() as db_session:
        today = datetime.now(timezone.utc).date()
        weeks_with_today: list[tuple[int, int]] = []  # (week_index, week_created + week_updated)

        for week in weeks:
            week_created, week_updated, week_skipped, week_warnings, week_sids = _persist_week_sessions(
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
            session_ids.extend(week_sids)
            has_today = any(
                _compute_session_date(plan_start, week.week_index, s.day_index) == today
                for s in week.sessions
            )
            if has_today and (week_created > 0 or week_updated > 0):
                weeks_with_today.append((week.week_index, week_created + week_updated))

        # Ensure race session is included on race date for race plans
        if ctx.plan_type == PlanType.RACE and ctx.target_date:
            race_date_obj = date.fromisoformat(ctx.target_date)
            race_session_id = _ensure_race_session(
                db_session=db_session,
                race_date=race_date_obj,
                plan_start=plan_start,
                ctx=ctx,
                user_id=user_id,
                weeks=weeks,
            )
            if race_session_id:
                session_ids.append(race_session_id)
                created += 1
                logger.info(
                    "Added race session on race date",
                    race_date=race_date_obj.isoformat(),
                    session_id=race_session_id,
                    user_id=user_id,
                )

        db_session.commit()

        # TODO D: DB-side sanity check (deferred). Optional: after commit, verify
        # count(PlannedSession.id.in_(session_ids)) == len(session_ids) in a fresh session.

    # Trigger daily decision regeneration only after successful full commit
    for week_index, _ in weeks_with_today:
        try:

            def _trigger_decision(today_arg=today, user=user_id, athlete=athlete_id):
                try:
                    asyncio.run(trigger_daily_decision_for_user(user, athlete, today_arg))
                except Exception as e:
                    logger.warning(f"[CALENDAR_PERSISTENCE] Background daily decision trigger failed: {e}")

            thread = threading.Thread(target=_trigger_decision, daemon=True)
            thread.start()
            logger.debug(
                f"[CALENDAR_PERSISTENCE] Triggered daily decision for user_id={user_id}, "
                f"athlete_id={athlete_id}, week_index={week_index}"
            )
        except Exception as e:
            logger.warning(f"[CALENDAR_PERSISTENCE] Failed to trigger daily decision for user {user_id}: {e}")

    warnings_preview = warnings[:5] if warnings else []
    logger.info(
        f"B7: Calendar persistence complete - created={created}, updated={updated}, "
        f"skipped={skipped}, session_ids={len(session_ids)}, warnings={len(warnings)} "
        f"(attempted to persist {total_sessions} sessions)",
        plan_id=plan_id,
        user_id=user_id,
        athlete_id=athlete_id,
        total_sessions_to_persist=total_sessions,
        created=created,
        updated=updated,
        skipped=skipped,
        session_ids_count=len(session_ids),
        warning_count=len(warnings),
        warnings=warnings_preview,
    )
    logger.info(
        "Persisted sessions to calendar",
        session_count=len(session_ids),
        created=created,
        updated=updated,
        skipped=skipped,
    )

    # Warn if no sessions were created or updated
    if created == 0 and updated == 0:
        logger.warning(
            f"B7: No sessions were created or updated during persistence - "
            f"attempted {total_sessions} sessions, all {skipped} were skipped. "
            f"Warnings: {warnings[:3] if warnings else 'none'}",
            plan_id=plan_id,
            user_id=user_id,
            athlete_id=athlete_id,
            total_sessions_to_persist=total_sessions,
            skipped=skipped,
            session_ids_count=len(session_ids),
            warnings=warnings,
        )

    return PersistResult(
        plan_id=plan_id,
        created=created,
        updated=updated,
        skipped=skipped,
        warnings=warnings,
        success=(created + updated) > 0,
        session_ids=session_ids,
    )
