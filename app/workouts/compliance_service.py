"""Compliance service for computing and persisting workout compliance.

Service layer for computing deterministic compliance metrics between
planned workouts and executed activities.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity
from app.workouts.compliance import compute_step_compliance
from app.workouts.execution_models import StepCompliance as StepComplianceModel
from app.workouts.execution_models import WorkoutComplianceSummary, WorkoutExecution
from app.workouts.models import Workout, WorkoutStep
from app.workouts.timeline import build_workout_timeline


class ComplianceService:
    """Service for workout compliance computation and persistence."""

    @staticmethod
    def get_execution(session: Session, workout_id: str) -> WorkoutExecution | None:
        """Get workout execution by workout ID.

        Args:
            session: Database session
            workout_id: Workout UUID

        Returns:
            WorkoutExecution if found, None otherwise
        """
        stmt = select(WorkoutExecution).where(WorkoutExecution.workout_id == workout_id).order_by(WorkoutExecution.created_at.desc())
        result = session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def load_workout(session: Session, workout_id: str) -> tuple[Workout, list[WorkoutStep]]:
        """Load workout and its steps.

        Args:
            session: Database session
            workout_id: Workout UUID

        Returns:
            Tuple of (Workout, list of WorkoutStep)

        Raises:
            ValueError: If workout not found
        """
        workout_stmt = select(Workout).where(Workout.id == workout_id)
        workout_result = session.execute(workout_stmt)
        workout = workout_result.scalar_one_or_none()

        if workout is None:
            raise ValueError(f"Workout {workout_id} not found")

        steps_stmt = select(WorkoutStep).where(WorkoutStep.workout_id == workout_id).order_by(WorkoutStep.step_index)
        steps_result = session.execute(steps_stmt)
        steps = list(steps_result.scalars().all())

        return (workout, steps)

    @staticmethod
    def load_activity(session: Session, activity_id: str) -> Activity:
        """Load activity by ID.

        Args:
            session: Database session
            activity_id: Activity UUID

        Returns:
            Activity model instance

        Raises:
            ValueError: If activity not found
        """
        stmt = select(Activity).where(Activity.id == activity_id)
        result = session.execute(stmt)
        activity = result.scalar_one_or_none()

        if activity is None:
            raise ValueError(f"Activity {activity_id} not found")

        return activity

    @staticmethod
    def compute_and_persist(session: Session, workout_id: str) -> WorkoutComplianceSummary:
        """Compute and persist compliance metrics for a workout execution.

        This method:
        1. Loads workout execution
        2. Loads workout and steps
        3. Loads activity
        4. Builds workout timeline
        5. Computes compliance for each step
        6. Persists step compliance records
        7. Computes and persists workout summary

        Args:
            session: Database session
            workout_id: Workout UUID

        Returns:
            WorkoutComplianceSummary model instance

        Raises:
            ValueError: If execution, workout, or activity not found
        """
        # Get execution
        execution = ComplianceService.get_execution(session, workout_id)
        if execution is None:
            raise ValueError(f"No execution found for workout {workout_id}")

        # Load workout and steps
        workout, steps = ComplianceService.load_workout(session, workout_id)

        # Load activity
        activity = ComplianceService.load_activity(session, execution.activity_id)

        # Get streams data
        streams_data = activity.streams_data
        if streams_data is None:
            # No streams data - create empty summary
            summary = WorkoutComplianceSummary(
                workout_id=workout_id,
                overall_compliance_pct=1.0,
                total_pause_seconds=0,
                completed=True,
            )
            session.add(summary)
            return summary

        # Build timeline
        timeline = build_workout_timeline(workout, steps)

        # Compute compliance for each segment
        step_compliance_records: list[StepComplianceModel] = []
        total_pause_seconds = 0
        weighted_compliance_sum = 0.0
        total_duration_sum = 0

        for segment in timeline.segments:
            # Find corresponding step
            step = next((s for s in steps if s.id == str(segment.step_id)), None)
            if step is None:
                continue

            # Compute compliance
            result = compute_step_compliance(
                step,
                streams_data,
                segment.start_second,
                segment.end_second,
            )

            # Create compliance record
            compliance_record = StepComplianceModel(
                workout_step_id=step.id,
                duration_seconds=result.duration_seconds,
                time_in_range_seconds=result.time_in_range_seconds,
                overshoot_seconds=result.overshoot_seconds,
                undershoot_seconds=result.undershoot_seconds,
                pause_seconds=result.pause_seconds,
                compliance_pct=result.compliance_pct,
            )
            step_compliance_records.append(compliance_record)
            session.add(compliance_record)

            # Accumulate for summary
            total_pause_seconds += result.pause_seconds
            weighted_compliance_sum += result.compliance_pct * result.duration_seconds
            total_duration_sum += result.duration_seconds

        # Compute overall compliance (weighted average)
        if total_duration_sum > 0:
            overall_compliance_pct = weighted_compliance_sum / total_duration_sum
        else:
            overall_compliance_pct = 1.0

        # Determine if completed (≥80% steps have non-zero duration)
        steps_with_duration = sum(1 for record in step_compliance_records if record.duration_seconds > 0)
        completed = steps_with_duration >= (0.8 * len(step_compliance_records)) if step_compliance_records else True

        # Create or update summary
        summary_stmt = select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == workout_id)
        summary_result = session.execute(summary_stmt)
        existing_summary = summary_result.scalar_one_or_none()

        if existing_summary:
            existing_summary.overall_compliance_pct = overall_compliance_pct
            existing_summary.total_pause_seconds = total_pause_seconds
            existing_summary.completed = completed
            summary = existing_summary
        else:
            summary = WorkoutComplianceSummary(
                workout_id=workout_id,
                overall_compliance_pct=overall_compliance_pct,
                total_pause_seconds=total_pause_seconds,
                completed=completed,
            )
            session.add(summary)

        return summary
