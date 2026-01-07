"""LLM-based attribute extraction from free text goals."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.services.llm.model import get_model

# Use cheap model for extraction
EXTRACTION_MODEL = "gpt-4o-mini"

# Prompt directory (app/coach/prompts/)
PROMPTS_DIR = Path(__file__).parent.parent / "coach" / "prompts"


class ExtractedRaceAttributes(BaseModel):
    """Extracted structured race attributes from free text goals."""

    event_type: str | None = Field(
        description="Type of event (e.g., 'marathon', 'half marathon', '5K', 'triathlon')",
        default=None,
    )
    event_date: str | None = Field(
        description="Event date in YYYY-MM-DD format (use YYYY-MM-XX if only month/year known)",
        default=None,
    )
    goal_time: str | None = Field(
        description="Goal time if mentioned (e.g., '3:30:00', 'sub-4', '1:45:00')",
        default=None,
    )
    distance: str | None = Field(
        description="Distance if mentioned (e.g., '26.2 miles', '13.1 miles', '5K')",
        default=None,
    )
    location: str | None = Field(
        description="Event location if mentioned",
        default=None,
    )


class ExtractedInjuryAttributes(BaseModel):
    """Extracted structured injury attributes from free text injury notes."""

    injury_type: str | None = Field(
        description="Type of injury (e.g., 'knee', 'IT band', 'shin splints', 'Achilles tendonitis')",
        default=None,
    )
    body_part: str | None = Field(
        description="Body part affected (e.g., 'left knee', 'right ankle', 'lower back')",
        default=None,
    )
    severity: str | None = Field(
        description="Severity level (e.g., 'mild', 'moderate', 'severe', 'recovered')",
        default=None,
    )
    recovery_status: str | None = Field(
        description="Current recovery status (e.g., 'fully recovered', 'ongoing', 'recurring', 'preventive care')",
        default=None,
    )
    restrictions: str | None = Field(
        description="Activity restrictions or limitations (e.g., 'avoid high impact', 'limit running distance', 'no hills')",
        default=None,
    )
    date_occurred: str | None = Field(
        description="When injury occurred (YYYY-MM-DD format, or relative like '6 months ago')",
        default=None,
    )


class GoalExtractionService:
    """Service for extracting structured attributes from free text."""

    def __init__(self) -> None:
        """Initialize the service."""
        self.model = get_model("openai", EXTRACTION_MODEL)

    def extract_race_attributes(self, goal_text: str) -> ExtractedRaceAttributes:
        """Extract structured race attributes from free text goal.

        Args:
            goal_text: Free text goal description (e.g., "marathon in April 2025")

        Returns:
            ExtractedRaceAttributes with structured data

        Raises:
            RuntimeError: If extraction fails
        """
        logger.info(f"Extracting race attributes from goal text: {goal_text[:50]}...")

        prompt_text = _load_extraction_prompt()
        agent = Agent(
            model=self.model,
            system_prompt=prompt_text,
            output_type=ExtractedRaceAttributes,
        )

        try:
            result = agent.run_sync(f"Extract race attributes from this goal: {goal_text}")
            extracted = result.output

            logger.info(
                f"Extraction successful: event_type={extracted.event_type}, "
                f"event_date={extracted.event_date}, goal_time={extracted.goal_time}",
            )
        except Exception as e:
            logger.error(f"Failed to extract race attributes: {e}", exc_info=True)
            # Return empty attributes on failure (non-blocking)
            return ExtractedRaceAttributes()
        else:
            return extracted


def _load_extraction_prompt() -> str:
    """Load the goal extraction prompt from file.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_path = PROMPTS_DIR / "goal_extraction.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _load_injury_extraction_prompt() -> str:
    """Load the injury extraction prompt from file.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_path = PROMPTS_DIR / "injury_extraction.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def extract_injury_attributes(injury_notes: str) -> ExtractedInjuryAttributes:
    """Extract structured injury attributes from free text injury notes.

    Args:
        injury_notes: Free text injury description

    Returns:
        ExtractedInjuryAttributes with structured data
    """
    if not injury_notes or not injury_notes.strip():
        return ExtractedInjuryAttributes()

    logger.info(f"Extracting injury attributes from notes: {injury_notes[:50]}...")

    prompt_text = _load_injury_extraction_prompt()
    agent = Agent(
        model=get_model("openai", EXTRACTION_MODEL),
        system_prompt=prompt_text,
        output_type=ExtractedInjuryAttributes,
    )

    try:
        result = agent.run_sync(f"Extract injury attributes from this description: {injury_notes}")
        extracted = result.output

        logger.info(
            f"Injury extraction successful: type={extracted.injury_type}, "
            f"body_part={extracted.body_part}, recovery_status={extracted.recovery_status}",
        )
    except Exception as e:
        logger.error(f"Failed to extract injury attributes: {e}", exc_info=True)
        # Return empty attributes on failure (non-blocking)
        return ExtractedInjuryAttributes()
    else:
        return extracted
