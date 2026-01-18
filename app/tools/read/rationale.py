"""Read-only access to rationale generation.

Generate human-readable explanations for coaching recommendations.
"""

from loguru import logger

from app.explanations.rationale import generate_rationale as _generate_rationale


def generate_plan_rationale(
    context: dict,
    compliance: dict,
    trends: dict,
    risks: list[dict],
    recommendation: dict,
    knowledge: list[dict] | None = None,  # Phase 6: Optional knowledge snippets
) -> dict:
    """Generate a human-readable rationale for a coaching recommendation.

    READ-ONLY: Human-readable explanation for a recommendation.
    This function creates structured explanations that can be shown
    directly to users.

    Args:
        context: Context dictionary (may include user_id, date, etc.)
        compliance: Compliance metrics from get_plan_compliance()
        trends: Trend data from get_metric_trends()
        risks: List of risk flags from get_risk_flags()
        recommendation: Recommendation dictionary (e.g., from recommend_no_change)
        knowledge: Optional list of knowledge snippets from RAG (Phase 6)

    Returns:
        Dictionary with summary, key_factors, what_went_well, concerns, recommendation,
        and optionally background (knowledge snippets)
    """
    logger.debug("Generating plan rationale")

    return _generate_rationale(
        _context=context,
        compliance=compliance,
        trends=trends,
        risks=risks,
        recommendation=recommendation,
        knowledge=knowledge,
    )
