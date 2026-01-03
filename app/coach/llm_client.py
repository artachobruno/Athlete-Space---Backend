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
from pathlib import Path
from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr, ValidationError

from app.coach.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent
from app.core.constraints import (
    validate_daily_decision,
    validate_season_plan,
    validate_weekly_intent,
)
from app.core.settings import settings

# Maximum retries for LLM calls
MAX_RETRIES = 2

# Prompt directory
PROMPTS_DIR = Path(__file__).parent / "prompts"


def _raise_validation_error(intent_type: str, error_msg: str) -> None:
    """Raise validation error after all retries exhausted.

    Args:
        intent_type: Type of intent (season_plan, weekly_intent, daily_decision)
        error_msg: Error message

    Raises:
        ValueError: Always raises
    """
    raise ValueError(f"{intent_type} validation failed after {MAX_RETRIES + 1} attempts: {error_msg}")


def _load_prompt(filename: str) -> str:
    """Load a prompt from the prompts directory.

    Args:
        filename: Name of the prompt file (e.g., "season_plan.txt")

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_path = PROMPTS_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _get_llm() -> ChatOpenAI:
    """Get configured LLM instance.

    Returns:
        Configured ChatOpenAI instance

    Raises:
        ValueError: If OPENAI_API_KEY is not configured
    """
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set. Please configure it in your .env file or environment variables.")
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.2,
        api_key=SecretStr(settings.openai_api_key),
    )


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
        self.llm = _get_llm()

    def generate_season_plan(self, context: dict[str, Any]) -> SeasonPlan:
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
        prompt_text = _load_prompt("season_plan.txt")
        parser = PydanticOutputParser(pydantic_object=SeasonPlan)

        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt_text),
            (
                "human",
                "Context:\n{context}\n\n{format_instructions}",
            ),
        ])

        chain = prompt | self.llm | parser

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating season plan (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                result = chain.invoke({
                    "context": context_str,
                    "format_instructions": parser.get_format_instructions(),
                })

                # Validate against constraints
                errors = validate_season_plan(result)
                if errors:
                    error_msg = "; ".join(errors)
                    if attempt < MAX_RETRIES:
                        logger.warning(f"Season plan validation failed: {error_msg}. Retrying...")
                        # Add error feedback to context for retry
                        context["validation_errors"] = error_msg
                        context_str = json.dumps(context, indent=2, default=str)
                        continue
                    _raise_validation_error("Season plan", error_msg)
                else:
                    logger.info("Season plan generated successfully")
                    return result

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

    def generate_weekly_intent(
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
        prompt_text = _load_prompt("weekly_intent.txt")
        parser = PydanticOutputParser(pydantic_object=WeeklyIntent)

        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt_text),
            (
                "human",
                "Context:\n{context}\n\n{format_instructions}",
            ),
        ])

        chain = prompt | self.llm | parser

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating weekly intent (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                result = chain.invoke({
                    "context": context_str,
                    "format_instructions": parser.get_format_instructions(),
                })

                # Validate against constraints
                errors = validate_weekly_intent(result, previous_volume)
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
                    return result

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

    def generate_daily_decision(self, context: dict[str, Any]) -> DailyDecision:
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
        prompt_text = _load_prompt("daily_decision.txt")
        parser = PydanticOutputParser(pydantic_object=DailyDecision)

        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt_text),
            (
                "human",
                "Context:\n{context}\n\n{format_instructions}",
            ),
        ])

        chain = prompt | self.llm | parser

        context_str = json.dumps(context, indent=2, default=str)

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(f"Generating daily decision (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                result = chain.invoke({
                    "context": context_str,
                    "format_instructions": parser.get_format_instructions(),
                })

                # Validate against constraints
                errors = validate_daily_decision(result)
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
                    return result

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
