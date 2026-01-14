"""Failure modes and safety handling for training intelligence.

Fail loudly and safely.
Never fabricate intent.
Never block API due to LLM failure.
"""

from datetime import date, datetime, timezone
from typing import Any

from loguru import logger

from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent
from app.services.intelligence.store import IntentStore


class IntelligenceFailureHandler:
    """Handles failures gracefully without blocking the API.

    Rules:
    - If LLM unavailable → return last valid intent
    - If no intent exists → return "coach unavailable" message
    - Never fabricate intent
    - Never block API due to LLM failure
    """

    def __init__(self) -> None:
        """Initialize failure handler."""
        self.store = IntentStore()

    def get_season_plan_with_fallback(
        self,
        athlete_id: int,
        include_inactive: bool = False,
    ) -> SeasonPlan | dict[str, Any] | None:
        """Get season plan with fallback to last valid version.

        Args:
            athlete_id: Athlete ID
            include_inactive: If True, also check inactive plans

        Returns:
            SeasonPlan if found, or dict with unavailable message, or None
        """
        plan_model = self.store.get_latest_season_plan(athlete_id, active_only=not include_inactive)

        if plan_model is None:
            logger.warning(
                "No season plan found, returning unavailable message",
                athlete_id=athlete_id,
            )
            return {
                "unavailable": True,
                "message": "Season plan not available. The coach is still learning about your training patterns.",
            }

        try:
            plan = SeasonPlan(**plan_model.plan_data)
        except Exception:
            logger.exception(
                f"Failed to parse season plan, returning unavailable (plan_id={plan_model.id}, athlete_id={athlete_id})"
            )
            return {
                "unavailable": True,
                "message": "Season plan data is corrupted. Please regenerate.",
            }
        else:
            logger.info(
                "Returning season plan (active or fallback)",
                plan_id=plan_model.id,
                athlete_id=athlete_id,
                is_active=plan_model.is_active,
            )
            return plan

    def get_weekly_intent_with_fallback(
        self,
        athlete_id: int,
        week_start: date,
        include_inactive: bool = False,
    ) -> WeeklyIntent | dict[str, Any] | None:
        """Get weekly intent with fallback to last valid version.

        Args:
            athlete_id: Athlete ID
            week_start: Week start date (Monday)
            include_inactive: If True, also check inactive intents

        Returns:
            WeeklyIntent if found, or dict with unavailable message, or None
        """
        week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        intent_model = self.store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=not include_inactive)

        if intent_model is None:
            logger.warning(
                "No weekly intent found, returning unavailable message",
                athlete_id=athlete_id,
                week_start=week_start.isoformat(),
            )
            return {
                "unavailable": True,
                "message": f"Weekly intent not available for week starting {week_start.isoformat()}. The coach will generate it soon.",
            }

        try:
            intent = WeeklyIntent(**intent_model.intent_data)
        except Exception:
            logger.exception(
                f"Failed to parse weekly intent, returning unavailable (intent_id={intent_model.id}, athlete_id={athlete_id})"
            )
            return {
                "unavailable": True,
                "message": "Weekly intent data is corrupted. Please regenerate.",
            }
        else:
            logger.info(
                "Returning weekly intent (active or fallback)",
                intent_id=intent_model.id,
                athlete_id=athlete_id,
                is_active=intent_model.is_active,
            )
            return intent

    def get_daily_decision_with_fallback(
        self,
        athlete_id: int,
        decision_date: date,
        include_inactive: bool = False,
    ) -> DailyDecision | dict[str, Any] | None:
        """Get daily decision with fallback to last valid version.

        Args:
            athlete_id: Athlete ID
            decision_date: Decision date
            include_inactive: If True, also check inactive decisions

        Returns:
            DailyDecision if found, or dict with unavailable message, or None
        """
        decision_date_dt = datetime.combine(decision_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        decision_model = self.store.get_latest_daily_decision(
            athlete_id,
            decision_date_dt,
            active_only=not include_inactive,
        )

        if decision_model is None:
            logger.warning(
                "No daily decision found, returning unavailable message",
                athlete_id=athlete_id,
                decision_date=decision_date.isoformat(),
            )
            return {
                "unavailable": True,
                "message": f"Daily decision not available for {decision_date.isoformat()}. The coach will generate it soon.",
            }

        try:
            decision = DailyDecision(**decision_model.decision_data)
        except Exception:
            logger.exception(
                f"Failed to parse daily decision, returning unavailable (decision_id={decision_model.id}, athlete_id={athlete_id})"
            )
            return {
                "unavailable": True,
                "message": "Daily decision data is corrupted. Please regenerate.",
            }
        else:
            logger.info(
                "Returning daily decision (active or fallback)",
                decision_id=decision_model.id,
                athlete_id=athlete_id,
                is_active=decision_model.is_active,
            )
            return decision
