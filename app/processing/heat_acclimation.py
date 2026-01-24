"""Heat acclimation computation module (v1.1).

Computes heat acclimation score based on recent heat exposure.
Uses last 21 days of eligible activities.

Eligibility rules (HARD):
- Outdoor activities
- Aerobic sports (run, ride)
- Duration >= 30 minutes
- heat_stress_index >= 0.50

Formula (LOCKED):
- session_heu = duration_minutes * heat_stress_index
- rolling_heu = sum(session_heu over 21 days)
- heat_acclimation_score = clamp(rolling_heu / 900, 0.0, 1.0)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity


def compute_heat_acclimation_score(
    session: Session,
    user_id: str,
    activity_date: datetime,
) -> float:
    """Compute heat acclimation score for an athlete.

    Uses last 21 days of eligible activities ending before activity_date.

    Args:
        session: Database session
        user_id: User ID
        activity_date: Date of the activity (used to determine lookback window)

    Returns:
        Heat acclimation score (0.0-1.0)
    """
    # Lookback window: 21 days before activity_date
    cutoff_date = activity_date - timedelta(days=21)

    # Fetch eligible activities
    eligible_activities = session.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.starts_at >= cutoff_date,
            Activity.starts_at < activity_date,
            Activity.sport.in_(["run", "ride"]),  # Aerobic sports only
            Activity.duration_seconds >= 30 * 60,  # >= 30 minutes
            Activity.heat_stress_index.isnot(None),
            Activity.heat_stress_index >= 0.50,  # Minimum heat stress
        )
        .order_by(Activity.starts_at.desc())
    ).scalars().all()

    if not eligible_activities:
        logger.debug(
            f"[HEAT_ACCLIMATION] No eligible activities for user {user_id} "
            f"in 21-day window ending at {activity_date.isoformat()}"
        )
        return 0.0

    # Compute rolling HEU (Heat Exposure Units)
    # IMPORTANT: Always use raw heat_stress_index, never effective_heat_stress_index
    # This prevents contamination/self-reinforcement in acclimation computation
    rolling_heu = 0.0
    for activity in eligible_activities:
        if activity.duration_seconds and activity.heat_stress_index is not None:
            duration_minutes = activity.duration_seconds / 60.0
            # Use raw heat_stress_index only (never effective_heat_stress_index)
            session_heu = duration_minutes * activity.heat_stress_index
            rolling_heu += session_heu

    # Compute acclimation score: clamp(rolling_heu / 900, 0.0, 1.0)
    heat_acclimation_score = max(0.0, min(1.0, rolling_heu / 900.0))

    logger.debug(
        f"[HEAT_ACCLIMATION] User {user_id}: {len(eligible_activities)} eligible activities, "
        f"rolling_heu={rolling_heu:.1f}, score={heat_acclimation_score:.3f}"
    )

    return heat_acclimation_score


def compute_effective_heat_stress_index(
    heat_stress_index: float,
    heat_acclimation_score: float,
) -> float:
    """Compute effective heat stress index accounting for acclimation.

    Formula (LOCKED):
    effective_hsi = heat_stress_index * (1 - 0.5 * heat_acclimation_score)

    Rules:
    - Max reduction = 50% (when acclimation_score = 1.0)
    - Never < 0
    - Never replaces raw HSI

    Args:
        heat_stress_index: Raw heat stress index (0.0-1.0)
        heat_acclimation_score: Heat acclimation score (0.0-1.0)

    Returns:
        Effective heat stress index (0.0-1.0)
    """
    if heat_stress_index <= 0.0:
        return 0.0

    # Apply acclimation reduction: max 50% reduction
    reduction_factor = 1.0 - (0.5 * heat_acclimation_score)
    effective_hsi = heat_stress_index * reduction_factor

    # Ensure never negative
    return max(0.0, effective_hsi)
