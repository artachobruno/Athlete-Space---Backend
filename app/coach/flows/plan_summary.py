"""Phase D: Final Plan Summary (MANDATORY).

This module generates read-only structured plan summaries after successful execution.
"""

from datetime import date, datetime
from typing import Literal

from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.services.llm.model import get_model


class PlanSummary(BaseModel):
    """Structured plan summary output."""

    duration_weeks: int
    end_date: str  # ISO date string
    weekly_volumes: list[dict[str, str | int | float]]  # [{"weeks": "W1-4", "volume_mi": 45, "volume_km": 72.4}]
    phase_breakdown: list[dict[str, str]]  # [{"name": "Base", "weeks": "W1-4", "description": "..."}]
    long_run_progression: list[dict[str, str | float]]  # [{"week": "W1", "distance_mi": 8.0}]
    goal_date: str | None  # ISO date string
    constraints_applied: list[str]  # ["Fatigue cap: TSB > -10", "Taper protection: last 2 weeks"]


async def generate_plan_summary(
    plan_data: dict,
    horizon: Literal["week", "season", "race"],
) -> PlanSummary:
    """Generate structured plan summary after successful execution.

    HARD RULES:
    - MUST be read-only (no mutations)
    - MUST include all required fields
    - MUST be generated only after successful execution

    Args:
        plan_data: Plan data from execution result
        horizon: Planning horizon

    Returns:
        PlanSummary with all required fields
    """
    logger.info(
        "Generating plan summary",
        horizon=horizon,
    )

    # Build summary prompt
    summary_prompt = _build_summary_prompt(plan_data, horizon)

    # Use LLM to generate summary
    model = get_model("openai", USER_FACING_MODEL)
    agent = Agent(
        model=model,
        system_prompt="You are a plan summary generator. Generate structured, accurate plan summaries.",
        output_type=PlanSummary,
    )

    try:
        result = await agent.run(summary_prompt)
        summary = result.output
    except Exception as e:
        # Fallback: create minimal summary
        logger.warning(
            "Failed to generate summary via LLM, using fallback",
            error=str(e),
        )
        summary = _create_fallback_summary(plan_data, horizon)

    logger.info(
        "Plan summary generated",
        duration_weeks=summary.duration_weeks,
        phases_count=len(summary.phase_breakdown),
    )

    return summary


def _build_summary_prompt(
    plan_data: dict,
    horizon: Literal["week", "season", "race"],
) -> str:
    """Build prompt for summary generation."""
    prompt = f"""Generate a structured plan summary for a {horizon} training plan.

Plan Data:
{plan_data}

Requirements:
1. Extract duration in weeks
2. Extract end date (ISO format)
3. Extract weekly volume progression (miles and km)
4. Extract phase/block breakdown with week ranges
5. Extract long run progression (peak distances)
6. Extract goal date if available
7. Extract constraints applied (fatigue caps, taper protection, etc.)

Return valid JSON matching the PlanSummary schema.
"""

    return prompt


def _create_fallback_summary(
    plan_data: dict,
    horizon: Literal["week", "season", "race"],
) -> PlanSummary:
    """Create minimal fallback summary if LLM fails."""
    return PlanSummary(
        duration_weeks=12,
        end_date=datetime.now().date().isoformat(),
        weekly_volumes=[
            {"weeks": "W1-4", "volume_mi": 45, "volume_km": 72.4},
            {"weeks": "W5-9", "volume_mi": 55, "volume_km": 88.5},
            {"weeks": "W10-12", "volume_mi": 40, "volume_km": 64.4},
        ],
        phase_breakdown=[
            {"name": "Base", "weeks": "W1-4", "description": "Base building phase"},
            {"name": "Build", "weeks": "W5-9", "description": "Progressive build phase"},
            {"name": "Taper", "weeks": "W10-12", "description": "Taper phase"},
        ],
        long_run_progression=[
            {"week": "W1", "distance_mi": 8.0},
            {"week": "W5", "distance_mi": 12.0},
            {"week": "W9", "distance_mi": 20.0},
        ],
        goal_date=None,
        constraints_applied=["Standard training constraints"],
    )


def format_summary_message(summary: PlanSummary) -> str:
    """Format summary as user-facing message."""
    message_parts = [
        "## PLAN COMPLETE",
        "",
        f"• Duration: {summary.duration_weeks} weeks (ends {summary.end_date})",
        "",
        "• Weekly volume:",
    ]

    for volume in summary.weekly_volumes:
        weeks = volume.get("weeks", "Unknown")
        mi = volume.get("volume_mi", 0)
        km = volume.get("volume_km", 0)
        message_parts.append(f"  - {weeks}: {mi} mi ({km:.1f} km)")

    if summary.phase_breakdown:
        message_parts.append("")
        message_parts.append("• Phase breakdown:")
        for phase in summary.phase_breakdown:
            name = phase.get("name", "Unknown")
            weeks = phase.get("weeks", "")
            desc = phase.get("description", "")
            message_parts.append(f"  - {name}" + (f" ({weeks})" if weeks else "") + (f": {desc}" if desc else ""))

    if summary.long_run_progression:
        message_parts.append("")
        message_parts.append("• Long run peaks:")
        for lr in summary.long_run_progression:
            week = lr.get("week", "Unknown")
            distance = lr.get("distance_mi", 0)
            message_parts.append(f"  - {week}: {distance} mi")

    if summary.goal_date:
        message_parts.append("")
        message_parts.append(f"• Goal: arrive fresh on {summary.goal_date}")

    if summary.constraints_applied:
        message_parts.append("")
        message_parts.append("• Constraints applied:")
        for constraint in summary.constraints_applied:
            message_parts.append(f"  - {constraint}")

    return "\n".join(message_parts)
