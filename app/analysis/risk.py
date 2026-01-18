"""Risk flag computation.

Identify risk conditions based on projections and reality.
Flags are descriptive, not prescriptive.
"""


def compute_risk_flags(
    projected_tsb: list[float],
    completion_pct: float,
    fatigue_scores: list[int] | None = None,
) -> list[dict]:
    """Identify risk conditions.

    Flags are descriptive, not prescriptive.
    Multiple flags allowed.
    Severity is informational only.

    Args:
        projected_tsb: List of projected TSB values
        completion_pct: Plan completion percentage (0.0 to 1.0)
        fatigue_scores: Optional list of fatigue scores from feedback (0-10 scale)

    Returns:
        List of risk flag dictionaries, each with:
        - type: Risk type identifier
        - severity: "high", "medium", or "low"
        - reason: Descriptive reason for the flag
    """
    flags = []

    # High fatigue risk: TSB projected to go very negative
    if projected_tsb and min(projected_tsb) < -25:
        flags.append(
            {
                "type": "high_fatigue",
                "severity": "high",
                "reason": "Projected TSB below -25",
            }
        )

    # Low compliance risk: Athlete not completing planned sessions
    if completion_pct < 0.6:
        flags.append(
            {
                "type": "low_compliance",
                "severity": "medium",
                "reason": "Less than 60% of sessions completed",
            }
        )

    # Subjective fatigue risk: High reported fatigue
    if fatigue_scores and max(fatigue_scores) >= 8:
        flags.append(
            {
                "type": "subjective_fatigue",
                "severity": "medium",
                "reason": "High reported fatigue (>= 8/10)",
            }
        )

    return flags
