"""Service for generating and persisting workout interpretations.

Handles LLM-based interpretation of workout executions with proper
error handling and graceful degradation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.workouts.execution_models import StepCompliance, WorkoutComplianceSummary
from app.workouts.llm.evaluator import WorkoutLLMEvaluator
from app.workouts.models import WorkoutStep


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
        if not step.target_metric:
            return "No specific target"

        metric_name = step.target_metric.upper()
        if step.target_min is not None and step.target_max is not None:
            return f"{metric_name} {step.target_min:.1f}-{step.target_max:.1f}"
        if step.target_value is not None:
            return f"{metric_name} {step.target_value:.1f}"

        return f"{metric_name} (zone {step.intensity_zone})" if step.intensity_zone else f"{metric_name} target"

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

            # Generate interpretation
            interpretation = await self.evaluator.evaluate_step(
                step_type=step.type,
                planned_target=planned_target,
                time_in_range_pct=time_in_range_pct,
                overshoot_pct=overshoot_pct,
                undershoot_pct=undershoot_pct,
                pause_seconds=compliance.pause_seconds,
                weather=None,  # TODO: Add weather context if available
                fatigue=None,  # TODO: Add fatigue context if available
            )

            if interpretation:
                # Persist step interpretation
                compliance.llm_rating = interpretation.rating
                compliance.llm_summary = interpretation.summary
                compliance.llm_tip = interpretation.coaching_tip
                compliance.llm_confidence = interpretation.confidence

                step_summaries.append(f"Step {step.step_index} ({step.type}): {interpretation.summary}")

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
