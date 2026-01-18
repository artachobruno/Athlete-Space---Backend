"""Read-only access to training load simulation.

Project training load forward assuming current plan is followed.
"""

from datetime import date

from loguru import logger

from app.analysis.simulation import simulate_training_load
from app.tools.read.metrics import get_training_metrics
from app.tools.read.plans import get_planned_activities


def simulate_training_load_forward(
    user_id: str,
    start: date,
    end: date,
    horizon_days: int = 14,
) -> dict:
    """Project training load forward.

    READ-ONLY: Project training load forward assuming planned sessions are executed.
    No modifications, no optimizations, pure projection.

    Args:
        user_id: User ID
        start: Start date for planned sessions query
        end: End date for planned sessions query
        horizon_days: Number of days to project forward (default: 14)

    Returns:
        Dictionary with projected_ctl, projected_atl, and projected_tsb lists
    """
    logger.debug(
        f"Simulating training load: user_id={user_id}, start={start}, end={end}, horizon_days={horizon_days}"
    )

    # Get planned activities in the date range
    planned = get_planned_activities(user_id, start, end)

    # Get current metrics
    metrics = get_training_metrics(user_id, start)

    # Simulate forward
    return simulate_training_load(planned, metrics, horizon_days)
