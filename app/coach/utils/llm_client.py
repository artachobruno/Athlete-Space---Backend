"""LLM client for generating training intents.

This layer does not "think" - it calls the LLM and validates outputs.
It handles:
- Loading system prompts
- Invoking the LLM
- Parsing JSON responses
- Validating against schemas and constraints
- Retrying on failure (up to 2 retries)
"""

import json
import time
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.coach.prompts.loader import load_prompt
from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent, WeeklyReport
from app.coach.schemas.training_plan_schemas import TrainingPlan
from app.core.constraints import (
    validate_daily_decision,
    validate_season_plan,
    validate_weekly_intent,
)
from app.services.llm.model import get_model

# Maximum retries for LLM calls
MAX_RETRIES = 2


def _raise_validation_error(intent_type: str, error_msg: str) -> None:
    """Raise validation error after all retries exhausted.

    Args:
        intent_type: Type of intent (season_plan, weekly_intent, daily_decision)
        error_msg: Error message

    Raises:
        ValueError: Always raises
    """
    raise ValueError(f"{intent_type} validation failed after {MAX_RETRIES + 1} attempts: {error_msg}")


def _get_model():
    """Get configured LLM model.

    Returns:
        Configured pydantic_ai model instance

    Raises:
        ValueError: If OPENAI_API_KEY is not configured
    """
    return get_model("openai", USER_FACING_MODEL)


class CoachLLMClient:
    """Client for generating training intents via LLM.

    This client handles:
    - Loading prompts
    - Invoking the LLM
    - Parsing and validating responses
    - Retrying on failure
    """

    def __init__(self) -> None:
        """Initialize the client."""
        self.model = _get_model()

    async def generate_season_plan(self, context: dict[str, Any]) -> SeasonPlan:
        """Generate a season plan from LLM.

        Args:
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
        prompt_text = await load_prompt("season_plan.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=SeasonPlan,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating season plan (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                user_prompt = f"Context:\n{context_str}"
                logger.debug(
                    "LLM Prompt: Season Plan Generation (attempt {attempt})\n"
                    "System Prompt:\n{system_prompt}\n\n"
                    "User Prompt:\n{user_prompt}",
                    system_prompt=prompt_text,
                    user_prompt=user_prompt,
                    attempt=attempt + 1,
                )
                result = await agent.run(user_prompt)

                # Validate against constraints
                errors = validate_season_plan(result.output)
                if errors:
                    error_msg = "; ".join(errors)
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Season plan validation failed: {error_msg}. Retrying...")
                        context["validation_errors"] = error_msg
                        context_str = json.dumps(context, indent=2, default=str)
                        continue
                    _raise_validation_error("Season plan", error_msg)
                else:
                    logger.info("Season plan generated successfully")
                    return result.output

            except ValidationError as e:
                if attempt < MAX_RETRIES:
                    logger.warning(f"Season plan parsing failed: {e}. Retrying...")
                    context["parsing_errors"] = str(e)
                    context_str = json.dumps(context, indent=2, default=str)
                    continue
                raise ValueError(f"Season plan parsing failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except Exception as e:
                logger.exception("Error generating season plan")
                if attempt < MAX_RETRIES:
                    continue
                raise RuntimeError(f"Failed to generate season plan: {type(e).__name__}: {e}") from e

        raise RuntimeError("Failed to generate season plan after all retries")

    async def generate_weekly_intent(
        self,
        context: dict[str, Any],
        previous_volume: float | None = None,
    ) -> WeeklyIntent:
        """Generate a weekly intent from LLM.

        Args:
            context: Context dictionary containing:
                - season_plan: Current SeasonPlan
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
        prompt_text = await load_prompt("weekly_intent.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=WeeklyIntent,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating weekly intent (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                user_prompt = f"Context:\n{context_str}"
                logger.debug(
                    "LLM Prompt: Weekly Intent Generation (attempt {attempt})\n"
                    "System Prompt:\n{system_prompt}\n\n"
                    "User Prompt:\n{user_prompt}",
                    system_prompt=prompt_text,
                    user_prompt=user_prompt,
                    attempt=attempt + 1,
                )
                result = await agent.run(user_prompt)

                # Validate against constraints
                errors = validate_weekly_intent(result.output, previous_volume)
                if errors:
                    error_msg = "; ".join(errors)
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Weekly intent validation failed: {error_msg}. Retrying...")
                        context["validation_errors"] = error_msg
                        context_str = json.dumps(context, indent=2, default=str)
                        continue
                    _raise_validation_error("Weekly intent", error_msg)
                else:
                    logger.info("Weekly intent generated successfully")
                    return result.output

            except ValidationError as e:
                if attempt < MAX_RETRIES:
                    logger.warning(f"Weekly intent parsing failed: {e}. Retrying...")
                    context["parsing_errors"] = str(e)
                    context_str = json.dumps(context, indent=2, default=str)
                    continue
                raise ValueError(f"Weekly intent parsing failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except Exception as e:
                logger.exception("Error generating weekly intent")
                if attempt < MAX_RETRIES:
                    continue
                raise RuntimeError(f"Failed to generate weekly intent: {type(e).__name__}: {e}") from e

        raise RuntimeError("Failed to generate weekly intent after all retries")

    async def generate_daily_decision(self, context: dict[str, Any]) -> DailyDecision:
        """Generate a daily decision from LLM.

        Args:
            context: Context dictionary containing:
                - weekly_intent: Current WeeklyIntent
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
        prompt_text = await load_prompt("daily_decision.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=DailyDecision,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"[DAILY_DECISION] LLM call starting (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                user_prompt = f"Context:\n{context_str}"
                logger.debug(
                    "LLM Prompt: Daily Decision Generation (attempt {attempt})\n"
                    "System Prompt:\n{system_prompt}\n\n"
                    "User Prompt:\n{user_prompt}",
                    system_prompt=prompt_text,
                    user_prompt=user_prompt,
                    attempt=attempt + 1,
                )
                result = await agent.run(user_prompt)

                # Validate against constraints
                errors = validate_daily_decision(result.output)
                if errors:
                    error_msg = "; ".join(errors)
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Daily decision validation failed: {error_msg}. Retrying...")
                        context["validation_errors"] = error_msg
                        context_str = json.dumps(context, indent=2, default=str)
                        continue
                    _raise_validation_error("Daily decision", error_msg)
                else:
                    logger.info(
                        f"[DAILY_DECISION] LLM call succeeded: recommendation={result.output.recommendation}, "
                        f"confidence={result.output.confidence.score:.2f}"
                    )
                    return result.output

            except ValidationError as e:
                if attempt < MAX_RETRIES:
                    logger.warning(f"[DAILY_DECISION] LLM parsing failed: {e}. Retrying...")
                    context["parsing_errors"] = str(e)
                    context_str = json.dumps(context, indent=2, default=str)
                    continue
                logger.warning(f"[DAILY_DECISION] LLM parsing failed after all retries: {e}")
                raise ValueError(f"Daily decision parsing failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except Exception as e:
                logger.exception(f"[DAILY_DECISION] LLM call error: {type(e).__name__}: {e}")
                if attempt < MAX_RETRIES:
                    continue
                raise RuntimeError(f"Failed to generate daily decision: {type(e).__name__}: {e}") from e

        raise RuntimeError("[DAILY_DECISION] Failed to generate daily decision after all retries")

    async def generate_weekly_report(self, context: dict[str, Any]) -> WeeklyReport:
        """Generate a weekly report from LLM.

        Args:
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
        prompt_text = await load_prompt("weekly_report.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=WeeklyReport,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating weekly report (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                user_prompt = f"Context:\n{context_str}"
                logger.debug(
                    "LLM Prompt: Weekly Report Generation (attempt {attempt})\n"
                    "System Prompt:\n{system_prompt}\n\n"
                    "User Prompt:\n{user_prompt}",
                    system_prompt=prompt_text,
                    user_prompt=user_prompt,
                    attempt=attempt + 1,
                )
                result = await agent.run(user_prompt)

                # Basic validation (schema validation is handled by pydantic)
                if not result.output.week_summary or len(result.output.week_summary) < 100:
                    error_msg = "Week summary too short"
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Weekly report validation failed: {error_msg}. Retrying...")
                        context["validation_errors"] = error_msg
                        context_str = json.dumps(context, indent=2, default=str)
                        continue
                    _raise_validation_error("Weekly report", error_msg)
                else:
                    logger.info("Weekly report generated successfully")
                    return result.output

            except ValidationError as e:
                if attempt < MAX_RETRIES:
                    logger.warning(f"Weekly report parsing failed: {e}. Retrying...")
                    context["parsing_errors"] = str(e)
                    context_str = json.dumps(context, indent=2, default=str)
                    continue
                raise ValueError(f"Weekly report parsing failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except Exception as e:
                logger.exception("Error generating weekly report")
                if attempt < MAX_RETRIES:
                    continue
                raise RuntimeError(f"Failed to generate weekly report: {type(e).__name__}: {e}") from e

        raise RuntimeError("Failed to generate weekly report after all retries")

    @staticmethod
    def _raise_validation_error(error_msg: str) -> None:
        """Raise validation error for training plan."""
        raise ValueError(error_msg)

    @staticmethod
    def _raise_plan_empty_error() -> None:
        """Raise error when plan has no sessions."""
        raise ValueError("Training plan must contain at least one session")

    @staticmethod
    def _raise_timezone_naive_error(title: str) -> None:
        """Raise error for timezone-naive date."""
        raise ValueError(f"All session dates must be timezone-aware. Session '{title}' has timezone-naive date")

    @staticmethod
    def _raise_duplicate_session_error(title: str, date_iso: str) -> None:
        """Raise error for duplicate session."""
        raise ValueError(f"Duplicate session detected: '{title}' on {date_iso}")

    def _validate_plan_sessions(self, plan: TrainingPlan, attempt: int) -> bool:
        """Validate plan sessions - check for empty, timezone-aware, duplicates.

        Returns:
            True if validation passed, False if should retry
        Raises:
            ValueError if validation failed and no more retries
        """
        logger.debug(
            "llm_client: _validate_plan_sessions - starting validation",
            attempt=attempt + 1,
            session_count=len(plan.sessions) if plan.sessions else 0,
        )

        # Phase 7: Plan guarantees enforced in code
        # ≥1 session exists
        if not plan.sessions:
            logger.debug(
                "llm_client: _validate_plan_sessions - no sessions found",
                attempt=attempt + 1,
                max_retries=MAX_RETRIES,
                will_retry=attempt < MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                logger.warning("Training plan validation failed: no sessions. Retrying with same inputs...")
                return False
            self._raise_plan_empty_error()

        logger.debug(
            "llm_client: _validate_plan_sessions - checking timezone and duplicates",
            attempt=attempt + 1,
            session_count=len(plan.sessions),
        )

        # Phase 6: Invariant checks - no duplicate dates+titles, all dates timezone-aware
        seen_dates_titles: set[tuple[datetime, str]] = set()
        for idx, sess in enumerate(plan.sessions):
            logger.debug(
                "llm_client: _validate_plan_sessions - validating session",
                attempt=attempt + 1,
                session_index=idx,
                session_title=sess.title,
                session_date=sess.date.isoformat() if sess.date else None,
                has_timezone=sess.date.tzinfo is not None if sess.date else False,
            )
            if sess.date.tzinfo is None:
                logger.debug(
                    "llm_client: _validate_plan_sessions - timezone-naive date found",
                    attempt=attempt + 1,
                    session_index=idx,
                    session_title=sess.title,
                    max_retries=MAX_RETRIES,
                    will_retry=attempt < MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Training plan validation failed: timezone-naive date for '{sess.title}'. "
                        "Retrying with same inputs..."
                    )
                    return False
                self._raise_timezone_naive_error(sess.title)

            date_title_key = (sess.date, sess.title)
            if date_title_key in seen_dates_titles:
                logger.debug(
                    "llm_client: _validate_plan_sessions - duplicate session found",
                    attempt=attempt + 1,
                    session_index=idx,
                    session_title=sess.title,
                    session_date=sess.date.isoformat(),
                    max_retries=MAX_RETRIES,
                    will_retry=attempt < MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Training plan validation failed: duplicate session '{sess.title}'. "
                        "Retrying with same inputs..."
                    )
                    return False
                self._raise_duplicate_session_error(sess.title, sess.date.isoformat())
            seen_dates_titles.add(date_title_key)

        logger.debug(
            "llm_client: _validate_plan_sessions - validation passed",
            attempt=attempt + 1,
            session_count=len(plan.sessions),
            unique_sessions=len(seen_dates_titles),
        )
        return True

    @staticmethod
    def _validate_plan_type_requirements(plan: TrainingPlan, goal_context: dict[str, Any]) -> str | None:
        """Validate plan type specific requirements.

        Returns:
            Error message if validation fails, None if valid
        """
        logger.debug(
            "llm_client: _validate_plan_type_requirements - starting validation",
            plan_type=plan.plan_type,
            session_count=len(plan.sessions),
            goal_context_keys=list(goal_context.keys()) if goal_context else [],
        )

        if plan.plan_type == "race":
            logger.debug(
                "llm_client: _validate_plan_type_requirements - validating race plan",
                has_race_date="race_date" in (goal_context or {}),
                session_count=len(plan.sessions),
            )
            # Race plans: race date must exist in plan
            race_date_str = goal_context.get("race_date")
            if race_date_str:
                logger.debug(
                    "llm_client: _validate_plan_type_requirements - checking race date in plan",
                    race_date_str=race_date_str,
                )
                try:
                    race_date = datetime.fromisoformat(race_date_str.replace("Z", "+00:00"))
                    race_sessions = [
                        s for s in plan.sessions
                        if s.date.date() == race_date.date() and s.intensity == "race"
                    ]
                    logger.debug(
                        "llm_client: _validate_plan_type_requirements - race session check",
                        race_date=race_date.date().isoformat(),
                        race_sessions_found=len(race_sessions),
                        race_session_titles=[s.title for s in race_sessions],
                    )
                    if not race_sessions:
                        logger.debug(
                            "llm_client: _validate_plan_type_requirements - race session missing",
                            race_date=race_date.date().isoformat(),
                        )
                        return "Race plan must include a race session on race day"
                except (ValueError, AttributeError) as e:
                    logger.debug(
                        "llm_client: _validate_plan_type_requirements - race date parsing failed",
                        race_date_str=race_date_str,
                        error=str(e),
                    )
                    pass  # Skip validation if date parsing fails

            # Race plans: must span ≥4 weeks
            if len(plan.sessions) > 0:
                dates = sorted([s.date.date() for s in plan.sessions])
                if len(dates) > 0:
                    span_days = (dates[-1] - dates[0]).days
                    logger.debug(
                        "llm_client: _validate_plan_type_requirements - checking race plan span",
                        first_date=dates[0].isoformat(),
                        last_date=dates[-1].isoformat(),
                        span_days=span_days,
                        required_days=28,
                        is_valid=span_days >= 28,
                    )
                    if span_days < 28:  # Less than 4 weeks
                        return f"Race plan must span at least 4 weeks (current: {span_days} days)"
        elif plan.plan_type == "season":
            logger.debug(
                "llm_client: _validate_plan_type_requirements - validating season plan",
                session_count=len(plan.sessions),
            )
            # Season plans: must span ≥4 weeks
            if len(plan.sessions) > 0:
                dates = sorted([s.date.date() for s in plan.sessions])
                if len(dates) > 0:
                    span_days = (dates[-1] - dates[0]).days
                    logger.debug(
                        "llm_client: _validate_plan_type_requirements - checking season plan span",
                        first_date=dates[0].isoformat(),
                        last_date=dates[-1].isoformat(),
                        span_days=span_days,
                        required_days=28,
                        is_valid=span_days >= 28,
                    )
                    if span_days < 28:  # Less than 4 weeks
                        return f"Season plan must span at least 4 weeks (current: {span_days} days)"
        # Future plan types (rehab, taper-only, diagnostics, weekly) - no span/race date requirements
        # Validation is keyed by plan_type for extensibility

        logger.debug(
            "llm_client: _validate_plan_type_requirements - validation passed",
            plan_type=plan.plan_type,
        )
        return None

    async def generate_training_plan_via_llm(
        self,
        *,
        user_context: dict[str, Any],
        athlete_context: dict[str, Any],
        goal_context: dict[str, Any],
        calendar_constraints: dict[str, Any],
    ) -> TrainingPlan:
        """Generate a complete training plan via LLM. Single entry point for all plan types.

        This is the ONLY function that generates training plans with sessions.
        All rule-based generation is removed - LLM is the only source of truth.

        Args:
            user_context: User context (preferences, goals, history)
            athlete_context: Athlete context (current fitness, training load, metrics)
            goal_context: Goal context (race distance, target time, plan type, dates)
            calendar_constraints: Calendar constraints (available dates, conflicts, preferences)

        Returns:
            Validated TrainingPlan with complete session list

        Raises:
            ValueError: If validation fails after all retries
            RuntimeError: If LLM call fails
        """
        t0 = time.monotonic()

        logger.debug(
            "llm_client: Starting generate_training_plan_via_llm",
            goal_context_keys=list(goal_context.keys()) if goal_context else [],
            user_context_keys=list(user_context.keys()) if user_context else [],
            athlete_context_keys=list(athlete_context.keys()) if athlete_context else [],
            calendar_constraints_keys=list(calendar_constraints.keys()) if calendar_constraints else [],
        )

        logger.debug("llm_client: Loading training plan generation prompt")
        prompt_text = await load_prompt("training_plan_generation.txt")
        logger.debug(
            "llm_client: Prompt loaded",
            prompt_length=len(prompt_text) if prompt_text else 0,
            prompt_preview=prompt_text[:200] if prompt_text else None,
        )

        logger.debug(
            "llm_client: Creating Agent instance",
            model_name=self.model.model_name if hasattr(self.model, "model_name") else type(self.model).__name__,
            output_type="TrainingPlan",
        )
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=TrainingPlan,
        )
        logger.debug("llm_client: Agent instance created")

        # Base context - preserve original inputs for deterministic retries
        logger.debug("llm_client: Building base context for LLM")
        base_context = {
            "user_context": user_context,
            "athlete_context": athlete_context,
            "goal_context": goal_context,
            "calendar_constraints": calendar_constraints,
        }
        context_str = json.dumps(base_context, indent=2, default=str)
        t1 = time.monotonic()
        context_load_time = t1 - t0
        logger.info(f"[PLAN] context_load={context_load_time:.1f}s")
        logger.debug(
            "llm_client: Context prepared",
            context_length=len(context_str),
            goal_context=goal_context,
            has_user_id="user_id" in (user_context or {}),
            has_athlete_id="athlete_id" in (user_context or {}),
        )

        logger.debug(
            "llm_client: Starting retry loop",
            max_retries=MAX_RETRIES,
            total_attempts=MAX_RETRIES + 1,
        )

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.debug(
                    "llm_client: Starting LLM call attempt",
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    context_length=len(context_str),
                )
                logger.info(f"Generating training plan via LLM (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                # Use base context for deterministic retries - no accumulated errors
                user_prompt = f"Context:\n{context_str}"
                full_prompt = f"{prompt_text}\n\n{user_prompt}"
                logger.debug(
                    "LLM Prompt: Training Plan Generation (attempt {attempt})\n"
                    "System Prompt:\n{system_prompt}\n\n"
                    "User Prompt:\n{user_prompt}\n\n"
                    "Full Prompt:\n{full_prompt}",
                    attempt=attempt + 1,
                    system_prompt=prompt_text,
                    user_prompt=user_prompt,
                    full_prompt=full_prompt,
                )
                logger.debug(
                    "llm_client: Calling agent.run",
                    attempt=attempt + 1,
                    prompt_prefix="Context:\n",
                    context_length=len(context_str),
                )
                t2_start = time.monotonic()
                result = await agent.run(user_prompt)
                t2 = time.monotonic()
                llm_generate_time = t2 - t2_start
                logger.info(f"[PLAN] llm_generate={llm_generate_time:.1f}s")
                logger.debug(
                    "llm_client: Agent.run completed",
                    attempt=attempt + 1,
                    has_output=bool(result.output) if result else False,
                    has_usage=getattr(result, "usage", None) is not None if result else False,
                )

                plan = result.output
                logger.debug(
                    "llm_client: Extracted plan from result",
                    attempt=attempt + 1,
                    plan_type=plan.plan_type if plan else None,
                    session_count=len(plan.sessions) if plan and plan.sessions else 0,
                    has_rationale=bool(plan.rationale) if plan else False,
                    assumptions_count=len(plan.assumptions) if plan and plan.assumptions else 0,
                )

                logger.debug(
                    "llm_client: Starting plan validation",
                    attempt=attempt + 1,
                    plan_type=plan.plan_type if plan else None,
                    session_count=len(plan.sessions) if plan and plan.sessions else 0,
                )

                # Validate plan sessions (extracted to reduce nesting)
                t3_start = time.monotonic()
                logger.debug(
                    "llm_client: Validating plan sessions",
                    attempt=attempt + 1,
                    session_count=len(plan.sessions) if plan and plan.sessions else 0,
                )
                validation_passed = self._validate_plan_sessions(plan, attempt)
                if not validation_passed:
                    logger.debug(
                        "llm_client: Plan session validation failed, retrying",
                        attempt=attempt + 1,
                        max_retries=MAX_RETRIES,
                        will_retry=attempt < MAX_RETRIES,
                    )
                    continue

                logger.debug(
                    "llm_client: Plan session validation passed",
                    attempt=attempt + 1,
                    session_count=len(plan.sessions),
                )

                # Phase 7: Validate plan type requirements (keyed by plan_type for flexibility)
                logger.debug(
                    "llm_client: Validating plan type requirements",
                    attempt=attempt + 1,
                    plan_type=plan.plan_type,
                    goal_context_keys=list(goal_context.keys()),
                )
                validation_error = self._validate_plan_type_requirements(plan, goal_context)
                t3 = time.monotonic()
                validation_time = t3 - t3_start
                logger.info(f"[PLAN] validation={validation_time:.1f}s")
                if validation_error:
                    logger.debug(
                        "llm_client: Plan type validation failed",
                        attempt=attempt + 1,
                        validation_error=validation_error,
                        max_retries=MAX_RETRIES,
                        will_retry=attempt < MAX_RETRIES,
                    )
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Training plan validation failed: {validation_error}. Retrying with same inputs...")
                        continue
                    self._raise_validation_error(validation_error)
                else:
                    logger.debug(
                        "llm_client: Plan type validation passed",
                        attempt=attempt + 1,
                        plan_type=plan.plan_type,
                    )
                    t_total = time.monotonic()
                    total_time = t_total - t0
                    logger.info(f"[PLAN] total={total_time:.1f}s")
                    logger.info(
                        "Training plan generated successfully",
                        plan_type=plan.plan_type,
                        session_count=len(plan.sessions),
                    )
                    logger.debug(
                        "llm_client: Training plan generation complete",
                        attempt=attempt + 1,
                        plan_type=plan.plan_type,
                        session_count=len(plan.sessions),
                        first_session_date=plan.sessions[0].date.isoformat() if plan.sessions else None,
                        last_session_date=plan.sessions[-1].date.isoformat() if plan.sessions else None,
                        rationale_length=len(plan.rationale) if plan.rationale else 0,
                        assumptions_count=len(plan.assumptions),
                    )
                    return plan

            except ValidationError as e:
                logger.debug(
                    "llm_client: ValidationError caught",
                    attempt=attempt + 1,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    error_count=len(e.errors()) if hasattr(e, "errors") else 0,
                    max_retries=MAX_RETRIES,
                    will_retry=attempt < MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    logger.warning(f"Training plan parsing failed: {e}. Retrying with same inputs...")
                    logger.debug(
                        "llm_client: Retrying after ValidationError",
                        attempt=attempt + 1,
                        next_attempt=attempt + 2,
                        using_same_inputs=True,
                    )
                    # Retry with identical inputs - deterministic behavior
                    continue
                logger.debug(
                    "llm_client: ValidationError - max retries reached",
                    attempt=attempt + 1,
                    total_attempts=MAX_RETRIES + 1,
                )
                raise ValueError(f"Training plan parsing failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except Exception as e:
                logger.debug(
                    "llm_client: Exception caught during plan generation",
                    attempt=attempt + 1,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    max_retries=MAX_RETRIES,
                    will_retry=attempt < MAX_RETRIES,
                )
                logger.exception("Error generating training plan")
                if attempt < MAX_RETRIES:
                    logger.debug(
                        "llm_client: Retrying after exception",
                        attempt=attempt + 1,
                        next_attempt=attempt + 2,
                        using_same_inputs=True,
                    )
                    # Retry with identical inputs - deterministic behavior
                    continue
                logger.debug(
                    "llm_client: Exception - max retries reached",
                    attempt=attempt + 1,
                    total_attempts=MAX_RETRIES + 1,
                )
                raise RuntimeError(f"Failed to generate training plan: {type(e).__name__}: {e}") from e

        logger.debug(
            "llm_client: All retries exhausted",
            total_attempts=MAX_RETRIES + 1,
        )
        raise RuntimeError("Failed to generate training plan after all retries")
