"""Core explainability logic for plan revisions.

This module generates human-readable explanations for plan modifications,
regenerations, and blocked actions. It is read-only and never mutates state.
"""

import json
from typing import Literal

from loguru import logger
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.coach.explainability.explanation_models import RevisionExplanation
from app.coach.explainability.explanation_prompt import build_revision_explanation_prompt
from app.plans.revision.types import PlanRevision
from app.services.llm.model import get_model


def _get_model():
    """Get configured LLM model for explanations.

    Returns:
        Configured pydantic_ai model instance
    """
    return get_model("openai", USER_FACING_MODEL)


async def explain_plan_revision(
    *,
    revision: PlanRevision,
    deltas: dict,
    athlete_profile: dict | None = None,
    constraints_triggered: list[str] | None = None,
) -> RevisionExplanation:
    """Generate human-readable explanation for a plan revision.

    This function is read-only and never mutates state. It only generates
    explanations based on the revision data.

    Args:
        revision: PlanRevision object (source of truth)
        deltas: Dictionary of changes (can be from revision.deltas or custom format)
        athlete_profile: Optional athlete profile dict with:
            - race_date: Optional race date (ISO format or datetime)
            - experience_level: Optional experience level
            - recent_fatigue: Optional fatigue indicators
        constraints_triggered: Optional list of constraint names that were triggered

    Returns:
        RevisionExplanation with human-readable explanation

    Raises:
        RuntimeError: If LLM call fails
    """
    logger.debug(
        "Generating revision explanation",
        revision_id=revision.revision_id,
        scope=revision.scope,
        outcome=revision.outcome,
    )

    # Determine revision type from outcome
    revision_type: Literal["MODIFY", "REGENERATE", "ROLLBACK", "BLOCKED"]
    if revision.outcome == "blocked":
        revision_type = "BLOCKED"
    elif revision.scope in {"day", "week", "season"}:
        revision_type = "MODIFY"
    elif revision.scope == "race" or "regenerate" in revision.user_request.lower():
        revision_type = "REGENERATE"
    else:
        revision_type = "MODIFY"

    # Format affected range
    if revision.affected_range:
        start = revision.affected_range.get("start", "")
        end = revision.affected_range.get("end", "")
        if start == end:
            affected_range = start
        else:
            affected_range = f"{start} to {end}"
    else:
        affected_range = "Unknown range"

    # Extract constraints from revision rules
    if constraints_triggered is None:
        constraints_triggered = [
            r.rule_id for r in revision.rules if r.triggered and r.severity in {"warning", "block"}
        ]

    # Build athlete context
    athlete_context: dict = {}
    if athlete_profile:
        if athlete_profile.get("race_date"):
            race_date = athlete_profile["race_date"]
            if hasattr(race_date, "isoformat"):
                athlete_context["race_date"] = race_date.isoformat()
            else:
                athlete_context["race_date"] = str(race_date)
        if "experience_level" in athlete_profile:
            athlete_context["experience_level"] = athlete_profile["experience_level"]
        if "recent_fatigue" in athlete_profile:
            athlete_context["recent_fatigue"] = athlete_profile["recent_fatigue"]

    # Build prompt
    prompt = build_revision_explanation_prompt(
        revision_type=revision_type,
        deltas=deltas,
        affected_range=affected_range,
        constraints_triggered=constraints_triggered,
        athlete_context=athlete_context,
    )

    # Call LLM with structured output
    model = _get_model()
    agent = Agent(
        model=model,
        system_prompt=prompt,
        output_type=RevisionExplanation,
    )

    try:
        logger.debug("Calling LLM for revision explanation", revision_id=revision.revision_id)
        result = await agent.run("Generate the explanation for this revision.")
        explanation = result.output

        # Ensure revision_type matches
        explanation.revision_type = revision_type

        logger.info(
            "Revision explanation generated",
            revision_id=revision.revision_id,
            revision_type=revision_type,
            summary_length=len(explanation.summary),
        )
    except Exception:
        logger.exception("Failed to generate revision explanation", revision_id=revision.revision_id)
        # Return fallback explanation
        explanation = RevisionExplanation(
            summary=f"Plan {revision.scope} was {revision.outcome}.",
            rationale=f"The requested change to your {revision.scope} plan was {revision.outcome}. "
            f"Reason: {revision.reason or 'User request'}.",
            safeguards=[r.rule_id for r in revision.rules if r.triggered],
            confidence="This change follows your training plan guidelines.",
            revision_type=revision_type,
        )

    return explanation
