"""Backfill script for coach feedback on planned sessions.

This script processes planned sessions that don't have coach feedback yet.
It generates coach feedback for both planned and completed sessions, using
the same LLM generation logic as the calendar/today endpoint.

Usage:
    From project root:
    python scripts/backfill_coach_feedback_for_planned_sessions.py [--no-dry-run] [--limit N] [--user-id USER_ID]

    Or as a module:
    python -m scripts.backfill_coach_feedback_for_planned_sessions [--no-dry-run] [--limit N] [--user-id USER_ID]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
    - Processes sessions in batches with rate limiting
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, CoachFeedback, PlannedSession, SessionLink
from app.db.session import SessionLocal
from app.workouts.llm.today_session_generator import generate_today_session_content
from app.workouts.models import Workout, WorkoutStep
from app.workouts.step_utils import infer_step_name
from app.workouts.targets_utils import get_distance_meters, get_duration_seconds, get_target_metric


async def _generate_coach_feedback_for_session(
    db: Session,
    planned_session: PlannedSession,
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Generate coach feedback for a single planned session.

    Args:
        db: Database session
        planned_session: Planned session to process
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
    """
    session_id_str = str(planned_session.id)

    # Check if feedback already exists
    existing_feedback = db.execute(
        select(CoachFeedback).where(CoachFeedback.planned_session_id == session_id_str)
    ).scalar_one_or_none()

    if existing_feedback:
        logger.debug(f"Session {session_id_str} already has coach feedback")
        stats["skipped_already_exists"] += 1
        return

    # Determine if session is completed
    is_completed = planned_session.status == "completed"

    # Get actual activity data if completed
    actual_duration_minutes: int | None = None
    actual_distance_km: float | None = None

    if is_completed:
        # Find paired activity via session_links
        session_link = db.execute(
            select(SessionLink).where(
                SessionLink.planned_session_id == session_id_str,
                SessionLink.status == "confirmed",
            )
        ).scalar_one_or_none()

        if session_link and session_link.activity_id:
            activity = db.get(Activity, session_link.activity_id)
            if activity:
                if activity.duration_seconds:
                    actual_duration_minutes = int(activity.duration_seconds // 60)
                if activity.distance_meters:
                    actual_distance_km = round(float(activity.distance_meters) / 1000.0, 2)
                logger.debug(
                    f"Found paired activity for session {session_id_str}: "
                    f"duration={actual_duration_minutes}m, distance={actual_distance_km}km"
                )

    # Extract session details for LLM generation
    session_title = planned_session.title or "Training Session"
    session_type = planned_session.type or "run"
    duration_seconds = planned_session.duration_seconds
    duration_minutes = int(duration_seconds // 60) if duration_seconds else None
    distance_meters = planned_session.distance_meters
    distance_km = round(float(distance_meters) / 1000.0, 2) if distance_meters else None
    intensity = planned_session.intensity
    notes = planned_session.notes
    intent = (planned_session.intent or "").lower()
    is_rest_day = intent == "rest" or "rest" in (session_title or "").lower()
    workout_id = str(planned_session.workout_id) if planned_session.workout_id else None

    # Load canonical workout steps if workout_id exists
    steps: list[dict[str, Any]] | None = None
    if workout_id:
        workout_obj = db.get(Workout, workout_id)
        if workout_obj:
            workout_steps = (
                db.execute(
                    select(WorkoutStep)
                    .where(WorkoutStep.workout_id == workout_id)
                    .order_by(WorkoutStep.step_index.asc())
                )
                .scalars()
                .all()
            )

            if workout_steps:
                # Convert WorkoutStep to schema format (matching _convert_workout_step_to_schema)
                steps = _convert_workout_steps_to_schema(list(workout_steps), workout_obj)
                logger.debug(f"Loaded {len(steps)} canonical steps for workout {workout_id}")

    # Early return for dry run - log what would be done
    if dry_run:
        logger.info(
            f"[DRY RUN] Would generate coach feedback for session {session_id_str}: "
            f"title={session_title}, type={session_type}, "
            f"is_completed={is_completed}, has_workout_steps={bool(steps)}"
        )
        stats["feedback_generated"] += 1
        return

    # Generate LLM content (all variables used here)
    try:
        content = await generate_today_session_content(
            session_title=session_title,
            session_type=session_type,
            duration_minutes=duration_minutes,
            distance_km=distance_km,
            intensity=intensity,
            notes=notes,
            is_rest_day=is_rest_day,
            is_completed=is_completed,
            actual_duration_minutes=actual_duration_minutes,
            actual_distance_km=actual_distance_km,
        )

        instructions = content.instructions
        if not steps:  # Only use LLM steps if we didn't get canonical steps
            steps = [
                {
                    "order": step.order,
                    "name": step.name,
                    "duration_min": step.duration_min,
                    "distance_km": step.distance_km,
                    "intensity": step.intensity,
                    "notes": step.notes,
                }
                for step in content.steps
            ]
        coach_insight = content.coach_insight

        # Persist coach feedback
        feedback = CoachFeedback(
            planned_session_id=session_id_str,
            user_id=planned_session.user_id,
            instructions=instructions or [],
            steps=steps or [],
            coach_insight=coach_insight or "",
        )

        db.add(feedback)
        db.flush()  # Flush to get immediate database errors

        logger.info(
            f"Generated coach feedback for session {session_id_str}: "
            f"instructions={len(instructions) if instructions else 0}, "
            f"steps={len(steps) if steps else 0}, "
            f"coach_insight_length={len(coach_insight) if coach_insight else 0}"
        )
        stats["feedback_generated"] += 1

    except Exception as e:
        logger.error(f"Failed to generate coach feedback for session {session_id_str}: {e}", exc_info=True)
        stats["errors"] += 1
        db.rollback()


def _convert_workout_steps_to_schema(
    workout_steps: list[WorkoutStep], workout_obj: Workout
) -> list[dict[str, Any]]:
    """Convert WorkoutStep list to schema format.

    Args:
        workout_steps: List of WorkoutStep objects
        workout_obj: Workout object for raw_notes context

    Returns:
        List of step dictionaries
    """
    steps = []
    for db_step in workout_steps:
        targets = db_step.targets or {}
        duration_seconds = get_duration_seconds(targets)
        duration_min = int(duration_seconds // 60) if duration_seconds else None
        distance_meters = get_distance_meters(targets)
        distance_km = round(distance_meters / 1000.0, 2) if distance_meters else None

        # Extract intensity from target metric or infer from step_type
        intensity_str = get_target_metric(targets)
        if not intensity_str and db_step.step_type:
            intensity_str = _infer_intensity_from_step_type(db_step.step_type)

        # Use purpose, inferred name, or step_type as name
        step_name = (
            db_step.purpose
            or infer_step_name(db_step, workout_obj.raw_notes if workout_obj else None)
            or db_step.step_type
            or f"Step {db_step.step_index + 1}"
        )

        steps.append({
            "order": db_step.step_index + 1,  # Convert 0-indexed to 1-indexed
            "name": step_name,
            "duration_min": duration_min,
            "distance_km": distance_km,
            "intensity": intensity_str,
            "notes": db_step.instructions,
        })

    return steps


def _infer_intensity_from_step_type(step_type: str | None) -> str | None:
    """Infer intensity from step type.

    Args:
        step_type: Step type string

    Returns:
        Intensity string or None
    """
    if not step_type:
        return None

    step_type_lower = step_type.lower()
    if "interval" in step_type_lower or "vo2" in step_type_lower:
        return "vo2"
    if "threshold" in step_type_lower or "tempo" in step_type_lower:
        return "threshold"
    if "steady" in step_type_lower:
        return "steady"
    if (
        "easy" in step_type_lower
        or "recovery" in step_type_lower
        or "warmup" in step_type_lower
        or "cooldown" in step_type_lower
    ):
        return "easy"
    return None


async def backfill_coach_feedback_for_planned_sessions(
    dry_run: bool = True,
    limit: int = 0,
    user_id: str | None = None,
) -> dict[str, int]:
    """Backfill coach feedback for planned sessions.

    Args:
        dry_run: If True, only log what would be done (default: True)
        limit: Maximum number of sessions to process (0 = no limit)
        user_id: Optional user ID to filter sessions (None = all users)

    Returns:
        Dictionary with counts of items processed
    """
    stats: dict[str, int] = {
        "sessions_found": 0,
        "feedback_generated": 0,
        "skipped_already_exists": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        logger.info(
            f"Starting coach feedback backfill for planned sessions "
            f"(dry_run={dry_run}, limit={limit}, user_id={user_id})"
        )

        # Find planned sessions without coach feedback
        query = (
            select(PlannedSession)
            .where(
                ~select(CoachFeedback.planned_session_id)
                .where(CoachFeedback.planned_session_id == PlannedSession.id)
                .exists()
            )
            .order_by(PlannedSession.starts_at.desc())
        )

        if user_id:
            query = query.where(PlannedSession.user_id == user_id)

        if limit > 0:
            query = query.limit(limit)

        sessions = db.execute(query).scalars().all()

        logger.info(f"Found {len(sessions)} planned sessions without coach feedback")
        stats["sessions_found"] = len(sessions)

        for i, session in enumerate(sessions):
            try:
                logger.info(f"Processing session {i + 1}/{len(sessions)}: {session.id}")
                await _generate_coach_feedback_for_session(db, session, stats, dry_run)

                # Commit every 10 sessions to avoid long transactions
                if not dry_run and (i + 1) % 10 == 0:
                    db.commit()
                    logger.debug(f"Committed batch of 10 sessions (processed {i + 1}/{len(sessions)})")

                # Rate limiting: small delay to avoid overwhelming the LLM API
                if i < len(sessions) - 1:  # Don't delay after last item
                    await asyncio.sleep(0.5)

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Error processing session {session.id}: {e}", exc_info=True)
                db.rollback()
                continue

        if not dry_run:
            db.commit()
            logger.info("Backfill complete - changes committed")
        else:
            logger.info("DRY RUN complete - no changes made")

        logger.info(
            f"Coach feedback backfill complete: "
            f"dry_run={dry_run}, "
            f"sessions_found={stats['sessions_found']}, "
            f"feedback_generated={stats['feedback_generated']}, "
            f"skipped_already_exists={stats['skipped_already_exists']}, "
            f"errors={stats['errors']}"
        )
    except Exception as e:
        db.rollback()
        logger.exception(f"Fatal error in backfill: {e}")
        raise
    else:
        return stats
    finally:
        db.close()


def main() -> int:
    """Main entry point for the backfill script."""
    parser = argparse.ArgumentParser(
        description="Backfill coach feedback for planned sessions",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the backfill (default: dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of sessions to process (0 = no limit)",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Optional user ID to filter sessions (default: all users)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    try:
        stats = asyncio.run(
            backfill_coach_feedback_for_planned_sessions(
                dry_run=dry_run,
                limit=args.limit,
                user_id=args.user_id,
            )
        )
        logger.info(f"Backfill completed successfully: {stats}")
    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
