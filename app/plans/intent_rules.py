"""Intent-pace constraints (non-enforcing).

Defines expected pace zones for each workout intent.
This becomes powerful in Step 3 for validation and reconciliation.

⚠️ Do NOT enforce yet - just define the mapping.
"""

from typing import Literal

from app.plans.types import WorkoutIntent

# Intent to allowed pace zones mapping
# This defines what pace zones are expected for each intent type
INTENT_PACE_CONSTRAINTS: dict[
    Literal["rest", "easy", "long", "quality"],
    dict[str, list[str]] | None,
] = {
    "rest": None,  # Rest days have no pace
    "easy": {
        "allowed_zones": ["recovery", "easy", "z1", "z2"],
    },
    "long": {
        "allowed_zones": ["easy", "steady", "mp"],
    },
    "quality": {
        "allowed_zones": [
            "lt1",
            "lt2",
            "tempo",
            "threshold",
            "mp",
            "hmp",
            "10k",
            "5k",
            "vo2max",
        ],
    },
}


def get_allowed_zones_for_intent(intent: str) -> list[str] | None:
    """Get allowed pace zones for a given intent.

    Args:
        intent: Workout intent (rest, easy, long, quality)

    Returns:
        List of allowed zone names, or None if no pace expected (rest)
    """
    if intent == "rest":
        constraints = INTENT_PACE_CONSTRAINTS.get("rest")
    elif intent == "easy":
        constraints = INTENT_PACE_CONSTRAINTS.get("easy")
    elif intent == "long":
        constraints = INTENT_PACE_CONSTRAINTS.get("long")
    elif intent == "quality":
        constraints = INTENT_PACE_CONSTRAINTS.get("quality")
    else:
        return None

    if constraints is None:
        return None
    return constraints.get("allowed_zones")
