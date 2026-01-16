"""Confidence scoring for plan revisions.

Deterministic scoring (NO LLM) based on diff characteristics.
"""

from app.coach.diff.diff_models import PlanDiff


def compute_revision_confidence(diff: PlanDiff) -> float:
    """Compute confidence score for a revision based on its diff.

    Confidence ranges from 0.0 (low) to 1.0 (high).

    Scoring rules:
    - Base score: 1.0
    - Plan scope: -0.3 (large changes are riskier)
    - Removed sessions: -0.2 per removed session (capped at -0.4)
    - Distance changes: -0.1 (significant metric changes)
    - Multiple field changes: -0.05 per additional field (beyond first)

    Args:
        diff: PlanDiff object representing the changes

    Returns:
        Confidence score between 0.0 and 1.0
    """
    score = 1.0

    # Scope penalty
    if diff.scope == "plan":
        score -= 0.3
    elif diff.scope == "week":
        score -= 0.1

    # Removed sessions penalty
    removed_count = len(diff.removed)
    if removed_count > 0:
        # Cap penalty at -0.4 for multiple removals
        removal_penalty = min(removed_count * 0.2, 0.4)
        score -= removal_penalty

    # Distance changes penalty
    has_distance_change = False
    total_field_changes = 0

    for modified in diff.modified:
        for change in modified.changes:
            total_field_changes += 1
            if "distance" in change.field.lower():
                has_distance_change = True

    if has_distance_change:
        score -= 0.1

    # Multiple field changes penalty
    if total_field_changes > 1:
        # -0.05 per additional field beyond the first
        additional_fields = total_field_changes - 1
        score -= min(additional_fields * 0.05, 0.2)

    # Ensure score stays in valid range
    return max(0.0, min(1.0, score))


def requires_approval(revision_type: str, confidence: float | None) -> bool:
    """Determine if a revision requires user approval.

    Rules:
    - REGENERATE always requires approval
    - RACE_CHANGE always requires approval
    - Low confidence (< 0.5) requires approval
    - MODIFY_DAY and MODIFY_WEEK are auto-applied if confidence >= 0.5

    Args:
        revision_type: Type of revision (modify_day, modify_week, regenerate_plan, etc.)
        confidence: Confidence score (0.0-1.0) or None

    Returns:
        True if revision requires approval, False otherwise
    """
    # Always require approval for risky operations
    if "regenerate" in revision_type.lower() or "race" in revision_type.lower():
        return True

    # Require approval for low confidence
    # MODIFY_DAY and MODIFY_WEEK are auto-applied if confidence is high enough
    return confidence is not None and confidence < 0.5
