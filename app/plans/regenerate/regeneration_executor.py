"""Executor for plan regeneration.

Deterministic mutation that replaces future sessions without touching history.
"""

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AthleteProfile, PlannedSession
from app.db.schema_v2_map import (
    combine_date_time,
    km_to_meters,
    mi_to_meters,
    minutes_to_seconds,
    normalize_sport,
)
from app.plans.regenerate.types import RegenerationRequest
from app.services.training_plan_service import plan_race


async def execute_regeneration(
    *,
    session: Session,
    athlete_profile: AthleteProfile,
    req: RegenerationRequest,
    revision_id: str,
    user_id: str,
    athlete_id: int,
) -> list[PlannedSession]:
    """Execute plan regeneration.

    Flow:
    1. Fetch future sessions in range
    2. Mark them as replaced (status="deleted" with note)
    3. Determine plan context from existing sessions
    4. Call plan generator
    5. Persist new sessions with revision_id

    Args:
        session: Database session
        athlete_profile: Athlete profile (for race date/distance)
        req: Regeneration request
        revision_id: Plan revision ID to attach to new sessions
        user_id: User ID
        athlete_id: Athlete ID

    Returns:
        List of newly created PlannedSession objects

    Raises:
        ValueError: If plan context cannot be determined
        RuntimeError: If plan generation fails
    """
    logger.info(
        "Executing regeneration",
        start_date=req.start_date.isoformat(),
        end_date=req.end_date.isoformat() if req.end_date else None,
        revision_id=revision_id,
    )

    # Step 1: Fetch future sessions to replace
    start_datetime = datetime.combine(req.start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_datetime = None
    if req.end_date:
        end_datetime = datetime.combine(req.end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    # Schema v2: use starts_at instead of date
    query = select(PlannedSession).where(
        PlannedSession.user_id == user_id,
        PlannedSession.starts_at >= start_datetime,
    )

    if end_datetime:
        query = query.where(PlannedSession.starts_at <= end_datetime)

    # Only replace non-completed sessions
    query = query.where(
        PlannedSession.status.notin_(["completed", "deleted", "skipped"]),
    )

    existing_sessions = list(session.execute(query).scalars().all())

    if not existing_sessions:
        logger.warning("No sessions to replace in regeneration range")
        return []

    # Step 2: Mark existing sessions as replaced
    session_ids = [s.id for s in existing_sessions]
    replacement_note = f"[Replaced by regeneration {revision_id}]"

    for existing_session in existing_sessions:
        existing_session.status = "deleted"
        if existing_session.notes:
            existing_session.notes = f"{existing_session.notes}\n{replacement_note}"
        else:
            existing_session.notes = replacement_note

    session.flush()

    logger.info(
        "Marked sessions as replaced",
        count=len(session_ids),
        revision_id=revision_id,
    )

    # Step 3: Determine plan context from existing sessions
    # Schema v2: plan_type is removed, use source and season_plan_id
    first_session = existing_sessions[0]
    source = getattr(first_session, "source", "planner_v2")  # Default source
    season_plan_id = getattr(first_session, "season_plan_id", None)  # Schema v2: season_plan_id instead of plan_id

    # Infer plan_type from source for backward compatibility with plan generators
    # TODO: Update plan generators to work with source instead of plan_type
    plan_type = "race" if "race" in source.lower() or season_plan_id is None else "season"

    # Step 4: Call plan generator based on plan_type
    # For now, we only support race plan regeneration
    # Season/week regeneration would need additional context extraction
    if plan_type == "race":
        race_date = athlete_profile.race_date
        if race_date is None:
            raise ValueError("Cannot regenerate race plan: no race_date in athlete profile")

        # Extract distance from season_plan_id or use default
        # season_plan_id format may be "race_{distance}_{date}" (legacy) or UUID
        distance = "Marathon"  # Default
        if season_plan_id:
            parts = season_plan_id.split("_")
            if len(parts) >= 2:
                distance = parts[1].replace("-", " ")

        # Normalize race_date to date, then convert to datetime for plan_race
        race_date_normalized = race_date.date() if hasattr(race_date, "date") else race_date
        race_datetime = datetime.combine(race_date_normalized, datetime.min.time()).replace(tzinfo=timezone.utc)

        # Call the same generator used in initial plan creation
        logger.info(
            "Calling plan generator for regeneration",
            plan_type=plan_type,
            race_date=race_date_normalized.isoformat(),
            distance=distance,
        )

        new_sessions_dict, _ = await plan_race(
            race_date=race_datetime,
            distance=distance,
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=start_datetime,
        )

        # Step 5: Filter new sessions to only those in our range and create PlannedSession objects
        # Schema v2: Convert to new field names
        new_sessions: list[PlannedSession] = []
        for session_dict in new_sessions_dict:
            session_date = session_dict.get("date")
            if isinstance(session_date, str):
                session_date = datetime.fromisoformat(session_date.replace("Z", "+00:00"))
            elif isinstance(session_date, datetime):
                pass
            elif not hasattr(session_date, "date"):
                continue

            # Ensure session_date is a datetime
            if hasattr(session_date, "date"):
                session_date_only = session_date.date()
            else:
                continue

            if session_date_only < req.start_date:
                continue
            if req.end_date and session_date_only > req.end_date:
                continue

            # Ensure session_date is a datetime object
            if isinstance(session_date, date):
                session_date = datetime.combine(session_date, datetime.min.time()).replace(tzinfo=timezone.utc)

            # Schema v2: Combine date and time into starts_at
            session_time = session_dict.get("time")
            starts_at = combine_date_time(session_date, session_time) if session_date else None
            if starts_at is None:
                continue

            # Schema v2: Convert units and normalize sport
            sport_raw = session_dict.get("type", "run")
            normalized_sport = normalize_sport(sport_raw)
            duration_minutes = session_dict.get("duration_minutes")
            duration_seconds = minutes_to_seconds(duration_minutes)
            distance_km = session_dict.get("distance_km")
            distance_mi = session_dict.get("distance_mi")
            distance_meters = None
            if distance_km is not None:
                distance_meters = km_to_meters(distance_km)
            elif distance_mi is not None:
                distance_meters = mi_to_meters(distance_mi)

            # Create PlannedSession from dict (schema v2)
            new_session = PlannedSession(
                user_id=user_id,
                starts_at=starts_at,  # Schema v2: combined date + time
                sport=normalized_sport,  # Schema v2: sport instead of type
                title=session_dict.get("title", session_dict.get("description", "")),
                duration_seconds=duration_seconds,  # Schema v2: duration_seconds instead of duration_minutes
                distance_meters=distance_meters,  # Schema v2: distance_meters instead of distance_km/distance_mi
                intensity=session_dict.get("intensity"),
                notes=session_dict.get("notes") or session_dict.get("description"),
                season_plan_id=season_plan_id,  # Schema v2: season_plan_id instead of plan_id
                revision_id=revision_id,  # Schema v2: revision_id links to plan_revisions
                session_type=session_dict.get("session_type"),
                intent=session_dict.get("intent"),
                philosophy_id=session_dict.get("philosophy_id"),
                template_id=session_dict.get("template_id"),
                source=session_dict.get("source", "planner_v2"),
                tags=session_dict.get("tags", []),  # Schema v2: tags is JSONB array
                status="planned",
            )

            new_sessions.append(new_session)
            session.add(new_session)

        session.flush()

        logger.info(
            "Regeneration complete",
            replaced_count=len(existing_sessions),
            new_count=len(new_sessions),
            revision_id=revision_id,
        )

        return new_sessions
    raise ValueError(
        f"Regeneration not yet supported for plan_type={plan_type}. "
        "Only 'race' plans are supported."
    )
