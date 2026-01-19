"""LLM evaluator for workout interpretation.

Evaluates workout executions using LLM to provide coaching feedback.
Results are cached and only run if compliance data exists.
"""

from __future__ import annotations

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.services.llm.model import get_model
from app.workouts.llm.prompts import build_step_prompt, build_workout_prompt
from app.workouts.llm.schemas import StepLLMInterpretation, WorkoutLLMInterpretation


class WorkoutLLMEvaluator:
    """Evaluator for generating LLM-based workout interpretations.

    This evaluator:
    - Only runs if compliance exists
    - Caches results (idempotent)
    - Allows re-run if compliance recalculated
    - Degrades gracefully on LLM failures
    """

    def __init__(self) -> None:
        """Initialize the evaluator."""
        self.model = get_model("openai", USER_FACING_MODEL)

    async def evaluate_step(
        self,
        step_type: str,
        planned_target: str,
        time_in_range_pct: float,
        overshoot_pct: float,
        undershoot_pct: float,
        pause_seconds: int,
        weather: str | None = None,
        fatigue: str | None = None,
    ) -> StepLLMInterpretation | None:
        """Evaluate a single workout step.

        Args:
            step_type: Step type (warmup, steady, interval, recovery, cooldown, free)
            planned_target: Human-readable planned target description
            time_in_range_pct: Percentage of time in target range (0-100)
            overshoot_pct: Percentage of time overshooting target (0-100)
            undershoot_pct: Percentage of time undershooting target (0-100)
            pause_seconds: Total pause time in seconds
            weather: Optional weather context
            fatigue: Optional fatigue context

        Returns:
            StepLLMInterpretation if successful, None on failure
        """
        try:
            prompt = build_step_prompt(
                step_type=step_type,
                planned_target=planned_target,
                time_in_range_pct=time_in_range_pct,
                overshoot_pct=overshoot_pct,
                undershoot_pct=undershoot_pct,
                pause_seconds=pause_seconds,
                weather=weather,
                fatigue=fatigue,
            )

            system_prompt = "You are a professional endurance coach providing workout execution feedback."
            agent = Agent(
                model=self.model,
                system_prompt=system_prompt,
                output_type=StepLLMInterpretation,
            )
            logger.debug(
                "LLM Prompt: Step Interpretation",
                system_prompt=system_prompt,
                user_prompt=prompt,
            )

            result = await agent.run(prompt)
            interpretation = result.output

            logger.info(
                "Step LLM interpretation generated",
                step_type=step_type,
                rating=interpretation.rating,
                confidence=interpretation.confidence,
            )
        except ValidationError as e:
            logger.error(
                "Step LLM interpretation validation failed",
                step_type=step_type,
                error=str(e),
            )
            return None
        except Exception as e:
            logger.error(
                "Step LLM interpretation failed",
                step_type=step_type,
                error=str(e),
            )
            return None
        else:
            return interpretation

    async def evaluate_workout(
        self,
        overall_compliance_pct: float,
        total_pause_seconds: int,
        completed: bool,
        step_summaries: list[str],
    ) -> WorkoutLLMInterpretation | None:
        """Evaluate a complete workout.

        Args:
            overall_compliance_pct: Overall compliance percentage (0-100)
            total_pause_seconds: Total pause time across all steps
            completed: Whether workout was completed
            step_summaries: List of brief step-level summaries

        Returns:
            WorkoutLLMInterpretation if successful, None on failure
        """
        try:
            prompt = build_workout_prompt(
                overall_compliance_pct=overall_compliance_pct,
                total_pause_seconds=total_pause_seconds,
                completed=completed,
                step_summaries=step_summaries,
            )

            system_prompt = "You are a professional endurance coach providing workout execution feedback."
            agent = Agent(
                model=self.model,
                system_prompt=system_prompt,
                output_type=WorkoutLLMInterpretation,
            )
            logger.debug(
                "LLM Prompt: Workout Interpretation",
                system_prompt=system_prompt,
                user_prompt=prompt,
            )

            result = await agent.run(prompt)
            interpretation = result.output

            logger.info(
                "Workout LLM interpretation generated",
                verdict=interpretation.verdict,
                overall_compliance_pct=overall_compliance_pct,
            )
        except ValidationError as e:
            logger.error(
                "Workout LLM interpretation validation failed",
                error=str(e),
            )
            return None
        except Exception as e:
            logger.error(
                "Workout LLM interpretation failed",
                error=str(e),
            )
            return None
        else:
            return interpretation
