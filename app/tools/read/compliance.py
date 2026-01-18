"""Read-only access to plan compliance.

Plan vs execution reconciliation.
"""

from datetime import date, datetime, timezone

from loguru import logger

from app.analysis.compliance import compute_plan_compliance
from app.tools.read.activities import get_completed_activities
from app.tools.read.plans import get_planned_activities


def get_plan_compliance(
    user_id: str,
    start: date,
    end: date,
) -> dict:
    """Get plan compliance metrics.

    READ-ONLY: Plan vs execution reconciliation.
    No writes, no suggestions, pure comparison.

    Args:
        user_id: User ID
        start: Start date (inclusive)
        end: End date (inclusive)

    Returns:
        Dictionary with compliance metrics
    """
    logger.debug(f"Reading plan compliance: user_id={user_id}, start={start}, end={end}")

    # Get planned activities
    planned = get_planned_activities(user_id, start, end)

    # Convert dates to datetimes for completed activities query
    start_datetime = datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_datetime = datetime.combine(end, datetime.max.time()).replace(tzinfo=timezone.utc)

    # Get completed activities
    completed = get_completed_activities(user_id, start_datetime, end_datetime)

    # Compute compliance
    return compute_plan_compliance(planned, completed)
