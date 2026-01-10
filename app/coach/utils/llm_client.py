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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.coach.mcp_client import MCPError, call_tool
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

# Prompt directory (go up two levels from utils/ to coach/, then into prompts/)
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _raise_validation_error(intent_type: str, error_msg: str) -> None:
    """Raise validation error after all retries exhausted.

    Args:
        intent_type: Type of intent (season_plan, weekly_intent, daily_decision)
        error_msg: Error message

    Raises:
        ValueError: Always raises
    """
    raise ValueError(f"{intent_type} validation failed after {MAX_RETRIES + 1} attempts: {error_msg}")


async def _load_prompt(filename: str) -> str:
    """Load a prompt from the prompts directory via MCP.

    Args:
        filename: Name of the prompt file (e.g., "season_plan.txt")

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    try:
        result = await call_tool("load_prompt", {"filename": filename})
        return result["content"]
    except MCPError as e:
        if e.code == "FILE_NOT_FOUND":
            raise FileNotFoundError(f"Prompt file not found: {filename}") from e
        raise RuntimeError(f"Failed to load prompt: {e.message}") from e


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
        prompt_text = await _load_prompt("season_plan.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=SeasonPlan,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating season plan (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                result = await agent.run(f"Context:\n{context_str}")

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
                logger.error(f"Error generating season plan: {type(e).__name__}: {e}", exc_info=True)
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
        prompt_text = await _load_prompt("weekly_intent.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=WeeklyIntent,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating weekly intent (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                result = await agent.run(f"Context:\n{context_str}")

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
                logger.error(f"Error generating weekly intent: {type(e).__name__}: {e}", exc_info=True)
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
        prompt_text = await _load_prompt("daily_decision.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=DailyDecision,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating daily decision (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                result = await agent.run(f"Context:\n{context_str}")

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
                    logger.info("Daily decision generated successfully")
                    return result.output

            except ValidationError as e:
                if attempt < MAX_RETRIES:
                    logger.warning(f"Daily decision parsing failed: {e}. Retrying...")
                    context["parsing_errors"] = str(e)
                    context_str = json.dumps(context, indent=2, default=str)
                    continue
                raise ValueError(f"Daily decision parsing failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except Exception as e:
                logger.error(f"Error generating daily decision: {type(e).__name__}: {e}", exc_info=True)
                if attempt < MAX_RETRIES:
                    continue
                raise RuntimeError(f"Failed to generate daily decision: {type(e).__name__}: {e}") from e

        raise RuntimeError("Failed to generate daily decision after all retries")

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
        prompt_text = await _load_prompt("weekly_report.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=WeeklyReport,
        )

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating weekly report (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                result = await agent.run(f"Context:\n{context_str}")

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
                logger.error(f"Error generating weekly report: {type(e).__name__}: {e}", exc_info=True)
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
        # Phase 7: Plan guarantees enforced in code
        # ≥1 session exists
        if not plan.sessions:
            if attempt < MAX_RETRIES:
                logger.warning("Training plan validation failed: no sessions. Retrying with same inputs...")
                return False
            self._raise_plan_empty_error()

        # Phase 6: Invariant checks - no duplicate dates+titles, all dates timezone-aware
        seen_dates_titles: set[tuple[datetime, str]] = set()
        for sess in plan.sessions:
            if sess.date.tzinfo is None:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Training plan validation failed: timezone-naive date for '{sess.title}'. "
                        "Retrying with same inputs..."
                    )
                    return False
                self._raise_timezone_naive_error(sess.title)

            date_title_key = (sess.date, sess.title)
            if date_title_key in seen_dates_titles:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Training plan validation failed: duplicate session '{sess.title}'. "
                        "Retrying with same inputs..."
                    )
                    return False
                self._raise_duplicate_session_error(sess.title, sess.date.isoformat())
            seen_dates_titles.add(date_title_key)
        return True

    @staticmethod
    def _validate_plan_type_requirements(plan: TrainingPlan, goal_context: dict[str, Any]) -> str | None:
        """Validate plan type specific requirements.

        Returns:
            Error message if validation fails, None if valid
        """
        if plan.plan_type == "race":
            # Race plans: race date must exist in plan
            race_date_str = goal_context.get("race_date")
            if race_date_str:
                try:
                    race_date = datetime.fromisoformat(race_date_str.replace("Z", "+00:00"))
                    race_sessions = [
                        s for s in plan.sessions
                        if s.date.date() == race_date.date() and s.intensity == "race"
                    ]
                    if not race_sessions:
                        return "Race plan must include a race session on race day"
                except (ValueError, AttributeError):
                    pass  # Skip validation if date parsing fails

            # Race plans: must span ≥4 weeks
            if len(plan.sessions) > 0:
                dates = sorted([s.date.date() for s in plan.sessions])
                if len(dates) > 0:
                    span_days = (dates[-1] - dates[0]).days
                    if span_days < 28:  # Less than 4 weeks
                        return f"Race plan must span at least 4 weeks (current: {span_days} days)"
        elif plan.plan_type == "season":
            # Season plans: must span ≥4 weeks
            if len(plan.sessions) > 0:
                dates = sorted([s.date.date() for s in plan.sessions])
                if len(dates) > 0:
                    span_days = (dates[-1] - dates[0]).days
                    if span_days < 28:  # Less than 4 weeks
                        return f"Season plan must span at least 4 weeks (current: {span_days} days)"
        # Future plan types (rehab, taper-only, diagnostics, weekly) - no span/race date requirements
        # Validation is keyed by plan_type for extensibility
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
        prompt_text = await _load_prompt("training_plan_generation.txt")
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=TrainingPlan,
        )

        # Base context - preserve original inputs for deterministic retries
        base_context = {
            "user_context": user_context,
            "athlete_context": athlete_context,
            "goal_context": goal_context,
            "calendar_constraints": calendar_constraints,
        }
        context_str = json.dumps(base_context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating training plan via LLM (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                # Use base context for deterministic retries - no accumulated errors
                result = await agent.run(f"Context:\n{context_str}")

                plan = result.output

                # Validate plan sessions (extracted to reduce nesting)
                if not self._validate_plan_sessions(plan, attempt):
                    continue

                # Phase 7: Validate plan type requirements (keyed by plan_type for flexibility)
                validation_error = self._validate_plan_type_requirements(plan, goal_context)
                if validation_error:
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Training plan validation failed: {validation_error}. Retrying with same inputs...")
                        continue
                    self._raise_validation_error(validation_error)
                else:
                    logger.info(
                        "Training plan generated successfully",
                        plan_type=plan.plan_type,
                        session_count=len(plan.sessions),
                    )
                    return plan

            except ValidationError as e:
                if attempt < MAX_RETRIES:
                    logger.warning(f"Training plan parsing failed: {e}. Retrying with same inputs...")
                    # Retry with identical inputs - deterministic behavior
                    continue
                raise ValueError(f"Training plan parsing failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except Exception as e:
                logger.error(f"Error generating training plan: {type(e).__name__}: {e}", exc_info=True)
                if attempt < MAX_RETRIES:
                    # Retry with identical inputs - deterministic behavior
                    continue
                raise RuntimeError(f"Failed to generate training plan: {type(e).__name__}: {e}") from e

        raise RuntimeError("Failed to generate training plan after all retries")
