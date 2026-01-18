"""Read-only access to risk flags.

Safety signals based on projections and reality.
"""

from datetime import date

from loguru import logger

from app.analysis.risk import compute_risk_flags
from app.tools.read.compliance import get_plan_compliance
from app.tools.read.feedback import get_subjective_feedback
from app.tools.read.simulation import simulate_training_load_forward


def get_risk_flags(
    user_id: str,
    start: date,
    end: date,
) -> list[dict]:
    """Get risk flags based on projections and reality.

    READ-ONLY: Safety signals based on projections and reality.
    Flags are descriptive, not prescriptive.

    Args:
        user_id: User ID
        start: Start date
        end: End date

    Returns:
        List of risk flag dictionaries
    """
    logger.debug(f"Computing risk flags: user_id={user_id}, start={start}, end={end}")

    # Get compliance metrics
    compliance = get_plan_compliance(user_id, start, end)

    # Get load simulation
    simulation = simulate_training_load_forward(user_id, start, end)

    # Get subjective feedback
    feedback = get_subjective_feedback(user_id, start, end)
    fatigue_scores = [f.fatigue for f in feedback if f.fatigue is not None] if feedback else None

    # Compute risk flags
    return compute_risk_flags(
        projected_tsb=simulation["projected_tsb"],
        completion_pct=compliance["completion_pct"],
        fatigue_scores=fatigue_scores,
    )
