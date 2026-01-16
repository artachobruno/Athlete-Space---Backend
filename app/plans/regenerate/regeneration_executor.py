"""Executor for plan regeneration.

Deterministic mutation that replaces future sessions without touching history.
"""

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AthleteProfile, PlannedSession
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
    2. Mark them as replaced (status="cancelled" with note)
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

    query = select(PlannedSession).where(
        PlannedSession.user_id == user_id,
        PlannedSession.athlete_id == athlete_id,
        PlannedSession.date >= start_datetime,
    )

    if end_datetime:
        query = query.where(PlannedSession.date <= end_datetime)

    # Only replace non-completed sessions
    query = query.where(
        PlannedSession.status.notin_(["completed", "cancelled", "skipped"]),
    )

    existing_sessions = list(session.execute(query).scalars().all())

    if not existing_sessions:
        logger.warning("No sessions to replace in regeneration range")
        return []

    # Step 2: Mark existing sessions as replaced
    session_ids = [s.id for s in existing_sessions]
    replacement_note = f"[Replaced by regeneration {revision_id}]"

    for existing_session in existing_sessions:
        existing_session.status = "cancelled"
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
    # Use the first session to infer plan_type and plan_id
    first_session = existing_sessions[0]
    plan_type = first_session.plan_type
    plan_id = first_session.plan_id

    # Step 4: Call plan generator based on plan_type
    # For now, we only support race plan regeneration
    # Season/week regeneration would need additional context extraction
    if plan_type == "race":
        race_date = athlete_profile.race_date
        if race_date is None:
            raise ValueError("Cannot regenerate race plan: no race_date in athlete profile")

        # Extract distance from plan_id or use default
        # plan_id format is typically "race_{distance}_{date}"
        distance = "Marathon"  # Default
        if plan_id:
            parts = plan_id.split("_")
            if len(parts) >= 2:
                distance = parts[1].replace("-", " ")

        # Convert race_date to datetime for plan_race
        race_datetime = datetime.combine(race_date, datetime.min.time()).replace(tzinfo=timezone.utc)

        # Call the same generator used in initial plan creation
        logger.info(
            "Calling plan generator for regeneration",
            plan_type=plan_type,
            race_date=race_date.isoformat(),
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

            # Create PlannedSession from dict (handle missing fields gracefully)
            new_session = PlannedSession(
                user_id=user_id,
                athlete_id=athlete_id,
                date=session_date,
                time=session_dict.get("time"),
                type=session_dict.get("type", "Run"),
                title=session_dict.get("title", session_dict.get("description", "")),
                duration_minutes=session_dict.get("duration_minutes"),
                distance_km=session_dict.get("distance_km"),
                intensity=session_dict.get("intensity"),
                notes=session_dict.get("notes") or session_dict.get("description"),
                plan_type=plan_type,
                plan_id=plan_id,
                week_number=session_dict.get("week_number"),
                session_order=session_dict.get("session_order"),
                phase=session_dict.get("phase"),
                source=session_dict.get("source", "planner_v2"),
                philosophy_id=session_dict.get("philosophy_id"),
                template_id=session_dict.get("template_id"),
                session_type=session_dict.get("session_type"),
                intent=session_dict.get("intent"),
                distance_mi=session_dict.get("distance_mi"),
                tags=session_dict.get("tags"),
                status="planned",
                revision_id=revision_id,
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
