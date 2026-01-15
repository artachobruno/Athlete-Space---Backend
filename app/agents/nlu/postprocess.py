"""Post-processing rules for NLU classification."""

from app.agents.nlu.types import NLUResult

MODIFY_VERBS = [
    "change",
    "move",
    "reduce",
    "increase",
    "adjust",
    "replace",
    "swap",
    "delete",
    "add",
]


def apply_disambiguation_rules(result: NLUResult, user_text: str) -> NLUResult:
    """Apply disambiguation rules to ensure correct intent classification.

    This function enforces that mutation verbs always result in MODIFY intent,
    preventing misclassification like "Change my week" → PLAN.

    Args:
        result: Initial NLU classification result
        user_text: Original user message text

    Returns:
        NLUResult with potentially corrected intent
    """
    user_text_lower = user_text.lower()

    # Force MODIFY when mutation verbs are present
    if any(verb in user_text_lower for verb in MODIFY_VERBS):
        result.intent = "modify"

    return result
