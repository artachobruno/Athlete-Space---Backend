"""Phase A: Plan Proposal Flow (NO MUTATION).

This module generates read-only plan proposals without any state mutations.
Proposals must be explicitly authorized before execution.
"""

from datetime import date, datetime
from typing import Literal

from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import USER_FACING_MODEL
from app.coach.schemas.athlete_state import AthleteState
from app.services.llm.model import get_model


class PlanProposal(BaseModel):
    """Structured plan proposal output."""

    type: Literal["plan_proposal"]
    scope: Literal["week", "season", "race"]
    overview: str
    blocks: list[dict[str, str]]  # Phase/block breakdown
    end_goal_date: str | None  # ISO date string or objective description
    changes: list[str]  # Explicit list of what will change
    authorization_required: bool = True
    assumptions: list[str] = []  # Key assumptions made


async def generate_plan_proposal(
    context: dict[str, str | int | float | bool | None],
    deps: CoachDeps,
    horizon: Literal["week", "season", "race"],
) -> PlanProposal:
    """Generate a read-only plan proposal.

    HARD RULES:
    - MUST NOT call any mutation tools
    - MUST NOT save anything to database
    - MUST NOT modify any state
    - MUST return structured proposal with explicit authorization question

    Args:
        context: Planning context (race_date, distance, target_time, etc.)
        deps: Coach dependencies
        horizon: Planning horizon (week, season, race)

    Returns:
        PlanProposal with all required fields
    """
    logger.info(
        "Generating plan proposal (Phase A - NO MUTATION)",
        horizon=horizon,
        athlete_id=deps.athlete_id,
        user_id=deps.user_id,
    )

    # Build proposal prompt
    proposal_prompt = _build_proposal_prompt(context, deps, horizon)

    # Use LLM to generate proposal (read-only, no tools)
    model = get_model("openai", USER_FACING_MODEL)
    agent = Agent(
        model=model,
        system_prompt="You are a training plan proposal generator. Generate structured plan proposals without executing any mutations.",
        output_type=PlanProposal,
    )

    try:
        # Generate proposal using structured output
        result = await agent.run(proposal_prompt)
        proposal = result.output
    except Exception as e:
        # Fallback: create minimal proposal
        logger.warning(
            "Failed to generate proposal via LLM, using fallback",
            error=str(e),
            error_type=type(e).__name__,
        )
        proposal_dict = _create_fallback_proposal(context, horizon)
        proposal = PlanProposal(**proposal_dict)

    # Ensure authorization_required is always True
    proposal.authorization_required = True

    logger.info(
        "Plan proposal generated",
        scope=proposal.scope,
        blocks_count=len(proposal.blocks),
        changes_count=len(proposal.changes),
    )

    return proposal


def _build_proposal_prompt(
    context: dict[str, str | int | float | bool | None],
    deps: CoachDeps,
    horizon: Literal["week", "season", "race"],
) -> str:
    """Build prompt for proposal generation."""
    # Extract key context
    race_date = context.get("race_date")
    distance = context.get("distance")
    target_time = context.get("target_time")

    # Build athlete context
    athlete_context = ""
    if deps.athlete_state:
        athlete_context = f"""
Current Training State:
- CTL: {deps.athlete_state.ctl:.1f}
- ATL: {deps.athlete_state.atl:.1f}
- TSB: {deps.athlete_state.tsb:.1f}
- Load trend: {deps.athlete_state.load_trend or 'stable'}
"""

    prompt = f"""Generate a plan proposal for a {horizon} training plan.

Context:
- Horizon: {horizon}
- Race date: {race_date or 'Not specified'}
- Distance: {distance or 'Not specified'}
- Target time: {target_time or 'Not specified'}
{athlete_context}

Requirements:
1. Generate a structured proposal with:
   - scope: "{horizon}"
   - overview: High-level description of the plan
   - blocks: List of training phases/blocks with descriptions
   - end_goal_date: Target date or objective
   - changes: Explicit list of what will change (e.g., "Replace current week plan", "Create new season plan")
   - assumptions: Key assumptions made (e.g., "Assumes 5 days/week availability", "Assumes no injuries")

2. This is a PROPOSAL ONLY - no execution, no mutations, no database writes.

3. End with explicit authorization requirement.

Return valid JSON matching the PlanProposal schema.
"""

    return prompt


def _create_fallback_proposal(
    context: dict[str, str | int | float | bool | None],
    horizon: Literal["week", "season", "race"],
) -> dict:
    """Create a minimal fallback proposal if LLM fails."""
    race_date = context.get("race_date")
    distance = context.get("distance")

    return {
        "type": "plan_proposal",
        "scope": horizon,
        "overview": f"Proposed {horizon} training plan",
        "blocks": [
            {"name": "Base", "description": "Base building phase"},
            {"name": "Build", "description": "Progressive build phase"},
            {"name": "Peak", "description": "Peak performance phase"},
        ],
        "end_goal_date": str(race_date) if race_date else None,
        "changes": [f"Create new {horizon} plan"],
        "authorization_required": True,
        "assumptions": [
            "Standard training availability",
            "No current injuries",
        ],
    }


def format_proposal_message(proposal: PlanProposal) -> str:
    """Format proposal as user-facing message with authorization question."""
    message_parts = [
        f"## {proposal.scope.title()} Plan Proposal",
        "",
        proposal.overview,
        "",
        "**Training Blocks:**",
    ]

    for block in proposal.blocks:
        name = block.get("name", "Unknown")
        desc = block.get("description", "")
        message_parts.append(f"- {name}: {desc}")

    if proposal.end_goal_date:
        message_parts.append(f"\n**Target Date:** {proposal.end_goal_date}")

    if proposal.assumptions:
        message_parts.append("\n**Assumptions:**")
        for assumption in proposal.assumptions:
            message_parts.append(f"- {assumption}")

    message_parts.append("\n**What will change:**")
    for change in proposal.changes:
        message_parts.append(f"- {change}")

    message_parts.append(
        "\n**Would you like me to proceed with creating this plan?** "
        "(Reply 'yes' or 'approve' to continue, or 'no' to cancel)"
    )

    return "\n".join(message_parts)
