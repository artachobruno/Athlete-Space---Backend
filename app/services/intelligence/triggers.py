"""Regeneration triggers for training intelligence.

Intelligence regenerates only when inputs change.
Regeneration is explicit and idempotent.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger

from app.services.intelligence.runtime import CoachRuntime
from app.services.intelligence.store import IntentStore


class RegenerationTriggers:
    """Manages when to regenerate training intelligence.

    Triggers:
    - New week boundary (for weekly intents)
    - Significant load change (for daily decisions)
    - Manual admin trigger (explicit regeneration)
    """

    def __init__(self) -> None:
        """Initialize triggers."""
        self.runtime = CoachRuntime()
        self.store = IntentStore()

    def should_regenerate_weekly_intent(
        self,
        athlete_id: int,
        week_start: date,
        current_context_hash: str,
    ) -> bool:
        """Check if weekly intent should be regenerated.

        Args:
            athlete_id: Athlete ID
            week_start: Week start date (Monday)
            current_context_hash: Hash of current context

        Returns:
            True if should regenerate, False otherwise
        """
        week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        existing = self.store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=True)

        if existing is None:
            logger.info(
                "Weekly intent does not exist, should regenerate",
                athlete_id=athlete_id,
                week_start=week_start.isoformat(),
            )
            return True

        # Check if context has changed significantly
        # For now, we regenerate if context hash is different
        # In the future, we could compare specific context fields
        stored_hash = existing.intent_data.get("_context_hash")
        if stored_hash != current_context_hash:
            logger.info(
                "Weekly intent context changed, should regenerate",
                athlete_id=athlete_id,
                week_start=week_start.isoformat(),
                old_hash=stored_hash,
                new_hash=current_context_hash,
            )
            return True

        logger.debug(
            "Weekly intent exists and context unchanged, no regeneration needed",
            athlete_id=athlete_id,
            week_start=week_start.isoformat(),
        )
        return False

    def should_regenerate_daily_decision(
        self,
        athlete_id: int,
        decision_date: date,
        current_context_hash: str,
    ) -> bool:
        """Check if daily decision should be regenerated.

        Args:
            athlete_id: Athlete ID
            decision_date: Decision date
            current_context_hash: Hash of current context

        Returns:
            True if should regenerate, False otherwise
        """
        decision_date_dt = datetime.combine(decision_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        existing = self.store.get_latest_daily_decision(athlete_id, decision_date_dt, active_only=True)

        if existing is None:
            logger.info(
                "Daily decision does not exist, should regenerate",
                athlete_id=athlete_id,
                decision_date=decision_date.isoformat(),
            )
            return True

        # Check if context has changed significantly
        stored_hash = existing.decision_data.get("_context_hash")
        if stored_hash != current_context_hash:
            logger.info(
                "Daily decision context changed, should regenerate",
                athlete_id=athlete_id,
                decision_date=decision_date.isoformat(),
                old_hash=stored_hash,
                new_hash=current_context_hash,
            )
            return True

        logger.debug(
            "Daily decision exists and context unchanged, no regeneration needed",
            athlete_id=athlete_id,
            decision_date=decision_date.isoformat(),
        )
        return False

    async def maybe_regenerate_weekly_intent(
        self,
        user_id: str,
        athlete_id: int,
        week_start: date,
        context: dict[str, Any],
        *,
        season_plan_id: str | None = None,
        previous_volume: float | None = None,
    ) -> str | None:
        """Regenerate weekly intent if needed.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            week_start: Week start date (Monday)
            context: Context dictionary
            season_plan_id: Optional season plan ID
            previous_volume: Previous week's volume

        Returns:
            Intent ID if regenerated, None if not needed
        """
        context_hash = self.runtime.compute_context_hash(context)

        if not self.should_regenerate_weekly_intent(athlete_id, week_start, context_hash):
            return None

        logger.info(
            "Regenerating weekly intent",
            user_id=user_id,
            athlete_id=athlete_id,
            week_start=week_start.isoformat(),
        )

        try:
            intent = await self.runtime.run_weekly_intent(user_id, athlete_id, context, previous_volume)
            intent_id = self.store.save_weekly_intent(
                user_id=user_id,
                athlete_id=athlete_id,
                intent=intent,
                season_plan_id=season_plan_id,
                context_hash=context_hash,
            )
        except Exception as e:
            logger.exception(
                f"Failed to regenerate weekly intent (user_id={user_id}, athlete_id={athlete_id})"
            )
            raise
        else:
            logger.info(
                "Weekly intent regenerated successfully",
                intent_id=intent_id,
                user_id=user_id,
                athlete_id=athlete_id,
            )
            return intent_id

    async def maybe_regenerate_daily_decision(
        self,
        user_id: str,
        athlete_id: int,
        decision_date: date,
        context: dict[str, Any],
        *,
        weekly_intent_id: str | None = None,
    ) -> str | None:
        """Regenerate daily decision if needed.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            decision_date: Decision date
            context: Context dictionary
            weekly_intent_id: Optional weekly intent ID

        Returns:
            Decision ID if regenerated, None if not needed
        """
        context_hash = self.runtime.compute_context_hash(context)

        if not self.should_regenerate_daily_decision(athlete_id, decision_date, context_hash):
            return None

        logger.info(
            "Regenerating daily decision",
            user_id=user_id,
            athlete_id=athlete_id,
            decision_date=decision_date.isoformat(),
        )

        try:
            decision = await self.runtime.run_daily_decision(user_id, athlete_id, context)
            decision_id = self.store.save_daily_decision(
                user_id=user_id,
                athlete_id=athlete_id,
                decision=decision,
                weekly_intent_id=weekly_intent_id,
                context_hash=context_hash,
            )
        except Exception as e:
            logger.exception(
                f"Failed to regenerate daily decision (user_id={user_id}, athlete_id={athlete_id})"
            )
            raise
        else:
            logger.info(
                "Daily decision regenerated successfully",
                decision_id=decision_id,
                user_id=user_id,
                athlete_id=athlete_id,
            )
            return decision_id

    @staticmethod
    def get_week_start(date_obj: date) -> date:
        """Get the Monday of the week for a given date.

        Args:
            date_obj: Date to get week start for

        Returns:
            Monday of the week
        """
        days_since_monday = date_obj.weekday()
        return date_obj - timedelta(days=days_since_monday)
