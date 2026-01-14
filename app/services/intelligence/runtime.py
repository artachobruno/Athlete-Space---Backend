"""LLM execution layer for training intelligence.

This layer only calls the LLM and returns parsed JSON.
It does NOT decide training - it executes LLM generation.
"""

import hashlib
import json
from typing import Any

from loguru import logger

from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent, WeeklyReport
from app.coach.utils.llm_client import CoachLLMClient


class CoachRuntime:
    """Runtime for executing LLM-based training intelligence.

    This class orchestrates:
    - Context building
    - LLM invocation
    - Result validation
    - Error handling

    It does NOT contain training logic - only execution.
    """

    def __init__(self) -> None:
        """Initialize the runtime."""
        self.llm_client = CoachLLMClient()

    async def run_season_plan(
        self,
        user_id: str,
        athlete_id: int,
        context: dict[str, Any],
    ) -> SeasonPlan:
        """Generate a season plan from LLM.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            context: Context dictionary containing:
                - athlete_state: Current athlete state
                - training_history: Recent training history
                - race_calendar: Target races and dates
                - athlete_goals: Performance goals and constraints
                - season_context: Time of year, base phase, etc.

        Returns:
            Validated SeasonPlan

        Raises:
            ValueError: If validation fails after all retries
            RuntimeError: If LLM call fails
        """
        logger.info(
            "Generating season plan",
            user_id=user_id,
            athlete_id=athlete_id,
        )

        try:
            plan = await self.llm_client.generate_season_plan(context)
        except Exception:
            logger.exception(
                f"Failed to generate season plan (user_id={user_id}, athlete_id={athlete_id})"
            )
            raise
        else:
            logger.info(
                "Season plan generated successfully",
                user_id=user_id,
                athlete_id=athlete_id,
                focus=plan.focus,
                confidence=plan.confidence,
            )
            return plan

    async def run_weekly_intent(
        self,
        user_id: str,
        athlete_id: int,
        context: dict[str, Any],
        previous_volume: float | None = None,
    ) -> WeeklyIntent:
        """Generate a weekly intent from LLM.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            context: Context dictionary containing:
                - season_plan: Current SeasonPlan (if exists)
                - training_history: Recent training history (last 2-4 weeks)
                - athlete_state: Current athlete state
                - week_context: Week number, time of year, upcoming events
                - recent_decisions: Recent daily decisions
            previous_volume: Previous week's volume in hours (for validation)

        Returns:
            Validated WeeklyIntent

        Raises:
            ValueError: If validation fails after all retries
            RuntimeError: If LLM call fails
        """
        logger.info(
            "Generating weekly intent",
            user_id=user_id,
            athlete_id=athlete_id,
            previous_volume=previous_volume,
        )

        try:
            intent = await self.llm_client.generate_weekly_intent(context, previous_volume)
        except Exception:
            logger.exception(
                f"Failed to generate weekly intent (user_id={user_id}, athlete_id={athlete_id})"
            )
            raise
        else:
            logger.info(
                "Weekly intent generated successfully",
                user_id=user_id,
                athlete_id=athlete_id,
                week_start=intent.week_start.isoformat(),
                volume_target=intent.volume_target_hours,
                confidence=intent.confidence,
            )
            return intent

    async def run_daily_decision(
        self,
        user_id: str,
        athlete_id: int,
        context: dict[str, Any],
    ) -> DailyDecision:
        """Generate a daily decision from LLM.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            context: Context dictionary containing:
                - weekly_intent: Current WeeklyIntent (if exists)
                - training_history: Recent training history (last 7-14 days)
                - athlete_state: Current athlete state
                - yesterday_training: What was done yesterday
                - day_context: Day of week, time of year, upcoming events
                - recent_decisions: Recent daily decisions

        Returns:
            Validated DailyDecision

        Raises:
            ValueError: If validation fails after all retries
            RuntimeError: If LLM call fails
        """
        logger.info(
            "Generating daily decision",
            user_id=user_id,
            athlete_id=athlete_id,
        )

        try:
            decision = await self.llm_client.generate_daily_decision(context)
        except Exception:
            logger.exception(
                f"Failed to generate daily decision (user_id={user_id}, athlete_id={athlete_id})"
            )
            raise
        else:
            logger.info(
                "Daily decision generated successfully",
                user_id=user_id,
                athlete_id=athlete_id,
                decision_date=decision.decision_date.isoformat(),
                recommendation=decision.recommendation,
                confidence_score=decision.confidence.score,
            )
            return decision

    async def run_weekly_report(
        self,
        user_id: str,
        athlete_id: int,
        context: dict[str, Any],
    ) -> WeeklyReport:
        """Generate a weekly report from LLM.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            context: Context dictionary containing:
                - weekly_intent: Current WeeklyIntent (what was planned)
                - actual_training: Actual training completed during the week
                - athlete_state: Current athlete state
                - previous_week_intent: Previous week's intent (for comparison)
                - week_context: Week number, time of year, upcoming events

        Returns:
            Validated WeeklyReport

        Raises:
            ValueError: If validation fails after all retries
            RuntimeError: If LLM call fails
        """
        logger.info(
            "Generating weekly report",
            user_id=user_id,
            athlete_id=athlete_id,
        )

        try:
            report = await self.llm_client.generate_weekly_report(context)
        except Exception:
            logger.exception(
                f"Failed to generate weekly report (user_id={user_id}, athlete_id={athlete_id})"
            )
            raise
        else:
            logger.info(
                "Weekly report generated successfully",
                user_id=user_id,
                athlete_id=athlete_id,
                week_start=report.week_start.isoformat(),
                week_end=report.week_end.isoformat(),
            )
            return report

    @staticmethod
    def compute_context_hash(context: dict[str, Any]) -> str:
        """Compute a hash of the context for change detection.

        Args:
            context: Context dictionary

        Returns:
            SHA256 hash of the context (hex string)
        """
        context_str = json.dumps(context, sort_keys=True, default=str)
        return hashlib.sha256(context_str.encode()).hexdigest()
