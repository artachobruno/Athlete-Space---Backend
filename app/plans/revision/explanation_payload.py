"""Explanation payload builder for LLM narration.

Builds structured payloads for LLM to explain PlanRevision changes.
LLM is ONLY used for narration - never for decision-making.
"""

from app.plans.revision.types import PlanRevision


def build_explanation_payload(
    *,
    revision: PlanRevision,
    athlete_context: dict,
) -> dict:
    """Build payload for LLM explanation generation.

    Args:
        revision: PlanRevision to explain
        athlete_context: Athlete context (profile, state, etc.)

    Returns:
        Dictionary payload for LLM explanation
    """
    return {
        "revision": revision.model_dump(mode="json"),
        "athlete": athlete_context,
        "instructions": {
            "style": "coach",
            "constraints": [
                "Do not invent rules",
                "Do not change facts",
                "Explain blocks clearly",
            ],
        },
    }
