"""Planned vs executed workout comparison.

This module provides deterministic comparison between planned workout steps
and executed activities. Pure math, no LLM.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select

from app.db.models import Activity
from app.db.session import get_session
from app.workouts.execution_models import WorkoutComplianceSummary, WorkoutExecution
from app.workouts.models import Workout, WorkoutStep


def _compute_step_comparison(
    planned_step: WorkoutStep,
    activity: Activity,
) -> dict[str, float | int | str]:
    """Compute comparison metrics for a single step.

    Args:
        planned_step: Planned workout step
        activity: Executed activity
        step_start_time: Step start time offset in seconds
        step_end_time: Step end time offset in seconds

    Returns:
        Dictionary with comparison metrics
    """
    # Get planned values
    planned_distance = planned_step.distance_meters or 0
    planned_duration = planned_step.duration_seconds or 0

    # Get executed values from activity streams (simplified - would need actual stream data)
    # For now, use activity totals as approximation
    executed_distance = activity.distance_meters or 0
    executed_duration = activity.duration_seconds or 0

    # Compute delta percentage
    delta_pct: float = 0.0
    if planned_distance > 0:
        delta_pct = ((executed_distance - planned_distance) / planned_distance) * 100
    elif planned_duration > 0:
        delta_pct = ((executed_duration - planned_duration) / planned_duration) * 100

    # Determine status
    tolerance = 10.0  # ±10%
    if abs(delta_pct) <= tolerance:
        status = "hit"
    elif delta_pct < -tolerance:
        status = "short"
    else:
        status = "over"

    return {
        "planned_distance": planned_distance,
        "executed_distance": executed_distance,
        "delta_pct": delta_pct,
        "status": status,
    }


def compute_workout_comparison(workout_id: str) -> None:
    """Compute and persist planned vs executed comparison.

    This function:
    1. Loads workout and steps
    2. Finds associated activity via WorkoutExecution
    3. Matches steps by order and type
    4. Computes deltas with ±10% tolerance
    5. Persists into workout_compliance_summary

    Args:
        workout_id: Workout ID to compare
    """
    try:
        with get_session() as session:
            # Load workout
            workout_stmt = select(Workout).where(Workout.id == workout_id)
            workout = session.execute(workout_stmt).scalar_one_or_none()

            if not workout:
                logger.warning(f"Workout {workout_id} not found, skipping comparison")
                return

            # Load steps
            steps_stmt = select(WorkoutStep).where(WorkoutStep.workout_id == workout_id).order_by(WorkoutStep.step_index)
            steps = list(session.execute(steps_stmt).scalars().all())

            if not steps:
                logger.debug(f"Workout {workout_id} has no steps, skipping comparison")
                return

            # Find execution (activity)
            execution_stmt = select(WorkoutExecution).where(WorkoutExecution.workout_id == workout_id).limit(1)
            execution = session.execute(execution_stmt).scalar_one_or_none()

            if not execution:
                logger.debug(f"Workout {workout_id} has no execution, skipping comparison")
                return

            # Load activity
            activity_stmt = select(Activity).where(Activity.id == execution.activity_id)
            activity = session.execute(activity_stmt).scalar_one_or_none()

            if not activity:
                logger.warning(f"Activity {execution.activity_id} not found for workout {workout_id}")
                return

            # Compute step-level comparisons
            step_comparisons: list[dict[str, float | int | str]] = []
            total_planned_distance = 0
            total_executed_distance = 0

            for step in steps:
                # Simple matching: use activity totals divided by step count
                # In a real implementation, this would use time-aligned stream data
                step_planned_distance = step.distance_meters or 0
                step_planned_duration = step.duration_seconds or 0

                # Initialize executed values
                step_executed_distance = 0
                step_executed_duration = 0

                # Approximate executed values (simplified)
                if step_planned_distance > 0:
                    # Distance-based: distribute activity distance proportionally
                    total_planned_distance += step_planned_distance
                elif step_planned_duration > 0:
                    # Duration-based: distribute activity duration proportionally
                    total_planned_duration = sum(s.duration_seconds or 0 for s in steps)
                    if total_planned_duration > 0:
                        step_executed_duration = int(
                            (step_planned_duration / total_planned_duration) * (activity.duration_seconds or 0)
                        )
                    else:
                        step_executed_duration = 0
                    step_executed_distance = int(
                        (step_executed_duration / (activity.duration_seconds or 1)) * (activity.distance_meters or 0)
                    )
                else:
                    step_executed_distance = 0
                    step_executed_duration = 0

                # Compute delta
                delta_pct: float = 0.0
                if step_planned_distance > 0:
                    total_planned_distance += step_planned_distance
                    if total_planned_distance > 0:
                        step_executed_distance = int(
                            (step_planned_distance / total_planned_distance) * (activity.distance_meters or 0)
                        )
                    delta_pct = (
                        ((step_executed_distance - step_planned_distance) / step_planned_distance) * 100
                        if step_planned_distance > 0
                        else 0.0
                    )
                elif step_planned_duration > 0:
                    total_planned_duration = sum(s.duration_seconds or 0 for s in steps)
                    if total_planned_duration > 0:
                        step_executed_duration = int(
                            (step_planned_duration / total_planned_duration) * (activity.duration_seconds or 0)
                        )
                    else:
                        step_executed_duration = 0
                    delta_pct = (
                        ((step_executed_duration - step_planned_duration) / step_planned_duration) * 100
                        if step_planned_duration > 0
                        else 0.0
                    )

                # Determine status
                tolerance = 10.0
                if abs(delta_pct) <= tolerance:
                    status = "hit"
                elif delta_pct < -tolerance:
                    status = "short"
                else:
                    status = "over"

                step_comparisons.append(
                    {
                        "step_id": step.id,
                        "step_order": step.step_index,
                        "planned_distance": step_planned_distance,
                        "executed_distance": step_executed_distance,
                        "delta_pct": delta_pct,
                        "status": status,
                    }
                )

                total_executed_distance += step_executed_distance

            # Compute overall compliance
            total_planned_distance = sum(s.distance_meters or 0 for s in steps)
            if total_planned_distance == 0:
                total_planned_duration = sum(s.duration_seconds or 0 for s in steps)
                if total_planned_duration > 0:
                    total_executed_duration = activity.duration_seconds or 0
                    overall_delta = (
                        ((total_executed_duration - total_planned_duration) / total_planned_duration) * 100
                        if total_planned_duration > 0
                        else 0.0
                    )
                else:
                    overall_delta = 0.0
            else:
                overall_delta = (
                    ((total_executed_distance - total_planned_distance) / total_planned_distance) * 100
                    if total_planned_distance > 0
                    else 0.0
                )

            overall_compliance_pct = max(0.0, min(100.0, 100.0 - abs(overall_delta)))
            completed = len([c for c in step_comparisons if c["status"] == "hit"]) >= len(steps) * 0.8

            # Persist summary
            summary_stmt = select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == workout_id)
            summary = session.execute(summary_stmt).scalar_one_or_none()

            if summary:
                summary.overall_compliance_pct = overall_compliance_pct
                summary.completed = completed
                summary.total_pause_seconds = 0  # Would need stream data to compute
            else:
                summary = WorkoutComplianceSummary(
                    workout_id=workout_id,
                    overall_compliance_pct=overall_compliance_pct,
                    total_pause_seconds=0,
                    completed=completed,
                )
                session.add(summary)

            session.flush()

            logger.info(
                "Workout comparison computed",
                workout_id=workout_id,
                overall_compliance_pct=overall_compliance_pct,
                completed=completed,
                step_count=len(step_comparisons),
            )

    except Exception:
        logger.exception(f"Error computing workout comparison for {workout_id}")
