"""Read-only recommendation tools.

Explicit recommendations including "no change".
"""

from loguru import logger


def recommend_no_change(reason: str) -> dict:
    """Explicit recommendation to keep plan unchanged.

    READ-ONLY: Explicit recommendation to keep plan unchanged.
    This lets the coach say "Given stable trends and no risk flags,
    the best action is no change." That's real coaching.

    Args:
        reason: Explanation for why no change is recommended

    Returns:
        Dictionary with recommendation type and reason
    """
    logger.debug(f"Recommending no change: {reason}")

    return {
        "recommendation": "no_change",
        "reason": reason,
    }
