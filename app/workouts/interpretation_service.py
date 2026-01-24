"""Service for generating and persisting workout interpretations.

Handles LLM-based interpretation of workout executions with proper
error handling and graceful degradation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.coach.utils.climate_feedback import get_climate_feedback_context
from app.db.models import Activity
from app.workouts.execution_models import StepCompliance, WorkoutComplianceSummary, WorkoutExecution
from app.workouts.llm.evaluator import WorkoutLLMEvaluator
from app.workouts.models import WorkoutStep
from app.workouts.targets_utils import get_target_max, get_target_metric, get_target_min, get_target_value


class InterpretationService:
    """Service for workout interpretation generation and persistence."""

    def __init__(self) -> None:
        """Initialize the service."""
        self.evaluator = WorkoutLLMEvaluator()

    @staticmethod
    def _format_planned_target(step: WorkoutStep) -> str:
        """Format planned target as human-readable string.

        Args:
            step: Workout step

        Returns:
            Human-readable target description
        """
        # Extract target data from targets JSONB (schema v2)
        targets = step.targets or {}
        target_metric = get_target_metric(targets)
        target_min = get_target_min(targets)
        target_max = get_target_max(targets)
        target_value = get_target_value(targets)

        if not target_metric:
            return "No specific target"

        metric_name = target_metric.upper()
        if target_min is not None and target_max is not None:
            return f"{metric_name} {target_min:.1f}-{target_max:.1f}"
        if target_value is not None:
            return f"{metric_name} {target_value:.1f}"

        return f"{metric_name} target"

    async def interpret_workout(self, session: Session, workout_id: str) -> bool:
        """Generate and persist interpretations for a workout.

        This method:
        1. Verifies compliance exists
        2. Generates step-level interpretations
        3. Generates workout-level interpretation
        4. Persists results

        Args:
            session: Database session
            workout_id: Workout UUID

        Returns:
            True if interpretation was successful, False otherwise

        Raises:
            ValueError: If compliance data not found
        """
        # Get summary
        summary_stmt = select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == workout_id)
        summary_result = session.execute(summary_stmt)
        summary = summary_result.scalar_one_or_none()

        if summary is None:
            raise ValueError(f"No compliance data found for workout {workout_id}. Compute compliance first.")

        # Get step compliance records with their steps
        steps_stmt = select(WorkoutStep).where(WorkoutStep.workout_id == workout_id).order_by(WorkoutStep.step_index)
        steps_result = session.execute(steps_stmt)
        steps = list(steps_result.scalars().all())

        if not steps:
            raise ValueError(f"No steps found for workout {workout_id}")

        step_ids = [step.id for step in steps]
        step_compliance_stmt = select(StepCompliance).where(StepCompliance.workout_step_id.in_(step_ids))
        step_compliance_result = session.execute(step_compliance_stmt)
        step_compliance_records = list(step_compliance_result.scalars().all())

        # Build step_id -> compliance map
        step_compliance_map = {record.workout_step_id: record for record in step_compliance_records}

        # Generate step-level interpretations
        step_summaries: list[str] = []
        for step in steps:
            compliance = step_compliance_map.get(step.id)
            if not compliance:
                continue

            # Calculate percentages
            duration = compliance.duration_seconds
            if duration == 0:
                continue

            time_in_range_pct = (compliance.time_in_range_seconds / duration) * 100.0
            overshoot_pct = (compliance.overshoot_seconds / duration) * 100.0
            undershoot_pct = (compliance.undershoot_seconds / duration) * 100.0

            planned_target = InterpretationService._format_planned_target(step)

            # Get climate context for activity if available
            weather_context = None
            workout_execution = (
                session.execute(select(WorkoutExecution).where(WorkoutExecution.workout_id == workout_id))
                .scalar_one_or_none()
            )
            if workout_execution and workout_execution.activity_id:
                activity = session.get(Activity, workout_execution.activity_id)
                if activity:
                    duration_min = (activity.duration_seconds / 60.0) if activity.duration_seconds else None
                    weather_context = get_climate_feedback_context(
                        activity_id=activity.id,
                        sport=activity.sport,
                        duration_min=duration_min,
                    )

            # Generate interpretation
            interpretation = await self.evaluator.evaluate_step(
                step_type=step.step_type,  # Use step_type from database model
                planned_target=planned_target,
                time_in_range_pct=time_in_range_pct,
                overshoot_pct=overshoot_pct,
                undershoot_pct=undershoot_pct,
                pause_seconds=compliance.pause_seconds,
                weather=weather_context,
                fatigue=None,  # TODO: Add fatigue context if available
            )

            if interpretation:
                # Persist step interpretation
                compliance.llm_rating = interpretation.rating
                compliance.llm_summary = interpretation.summary
                compliance.llm_tip = interpretation.coaching_tip
                compliance.llm_confidence = interpretation.confidence

                step_summaries.append(f"Step {step.step_index} ({step.step_type}): {interpretation.summary}")

        # Generate workout-level interpretation
        workout_interpretation = await self.evaluator.evaluate_workout(
            overall_compliance_pct=summary.overall_compliance_pct * 100.0,
            total_pause_seconds=summary.total_pause_seconds,
            completed=summary.completed,
            step_summaries=step_summaries,
        )

        if workout_interpretation:
            # Persist workout interpretation
            summary.llm_summary = workout_interpretation.summary
            summary.llm_verdict = workout_interpretation.verdict

        return True
