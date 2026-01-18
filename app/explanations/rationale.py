"""Rationale generation for coaching recommendations.

Generates human-readable explanations for coaching decisions.
Not chain-of-thought, but structured explanation artifacts.
"""


def generate_rationale(
    _context: dict,  # Kept for API compatibility, not used internally
    compliance: dict,
    trends: dict,
    risks: list[dict],
    recommendation: dict,
    knowledge: list[dict] | None = None,  # Phase 6: Optional RAG knowledge snippets
) -> dict:
    """Generate a human-readable rationale for a coaching recommendation.

    This function creates a structured explanation that can be shown
    directly to users. It is deterministic and safe.

    Args:
        _context: Context dictionary (reserved for future use, not currently used)
        compliance: Compliance metrics from get_plan_compliance()
        trends: Trend data from get_metric_trends()
        risks: List of risk flags from get_risk_flags()
        recommendation: Recommendation dictionary (e.g., from recommend_no_change)
        knowledge: Optional list of knowledge snippets from RAG (Phase 6)

    Returns:
        Dictionary with:
        - summary: High-level summary of the recommendation
        - key_factors: List of key factors considered
        - what_went_well: List of positive observations
        - concerns: List of concerns or issues
        - recommendation: The original recommendation
        - background: Optional list of knowledge snippets (if knowledge provided)
    """
    rationale = {
        "summary": "",
        "key_factors": [],
        "what_went_well": [],
        "concerns": [],
        "recommendation": recommendation,
    }

    # Summary
    rationale["summary"] = (
        "This recommendation is based on your recent training execution, "
        "projected load, and reported fatigue."
    )

    # Compliance analysis
    completion_pct = compliance.get("completion_pct", 0.0)
    if completion_pct >= 0.8:
        rationale["what_went_well"].append(
            "You completed most of your planned sessions."
        )
    elif completion_pct >= 0.6:
        rationale["concerns"].append(
            "Some planned sessions were missed recently."
        )
    else:
        rationale["concerns"].append(
            "Several planned sessions were missed recently."
        )

    # Trend analysis
    trend_direction = trends.get("direction", "unknown")
    if trend_direction == "up":
        rationale["what_went_well"].append(
            "Your training load is trending upward."
        )
        rationale["key_factors"].append("Increasing training load trend")
    elif trend_direction == "down":
        rationale["concerns"].append(
            "Your training load has been declining."
        )
        rationale["key_factors"].append("Declining training load trend")
    elif trend_direction == "flat":
        rationale["key_factors"].append("Stable training load")

    # Risk analysis
    for flag in risks:
        rationale["concerns"].append(flag["reason"])
        rationale["key_factors"].append(f"Risk: {flag['type']} ({flag['severity']} severity)")

    # Load delta analysis (if available)
    load_delta = compliance.get("load_delta", 0.0)
    if load_delta > 10:
        rationale["key_factors"].append(
            f"Completed load exceeded planned by {load_delta:.1f} points"
        )
    elif load_delta < -10:
        rationale["key_factors"].append(
            f"Completed load fell short of planned by {abs(load_delta):.1f} points"
        )

    # Phase 6: Include knowledge snippets if provided (educational background only)
    if knowledge:
        rationale["background"] = [
            {
                "title": k.get("title", "Untitled"),
                "excerpt": k.get("excerpt", ""),
                "source": k.get("source", "internal"),
            }
            for k in knowledge
            if isinstance(k, dict) and k.get("title")
        ]

    return rationale
