"""Training API endpoints - Phase 1: Mock data implementation.

These endpoints return mock data to establish the API contract before
implementing real data logic.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from loguru import logger

from app.api.schemas import (
    TrainingDistributionResponse,
    TrainingDistributionZone,
    TrainingSignal,
    TrainingSignalsResponse,
    TrainingStateMetrics,
    TrainingStateResponse,
)
from app.core.auth import get_current_user

router = APIRouter(prefix="/training", tags=["training"])


@router.get("/state", response_model=TrainingStateResponse)
def get_training_state(user_id: str = Depends(get_current_user)):
    """Get current training state and metrics.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        TrainingStateResponse with current training state
    """
    logger.info(f"[API] /training/state endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    # Use user_id hash to make metrics user-specific
    user_hash = hash(user_id) % 1000
    base_ctl = 65.5 + (user_hash % 20) - 10
    base_atl = 58.2 + (user_hash % 15) - 7
    base_tsb = base_ctl - base_atl

    return TrainingStateResponse(
        current=TrainingStateMetrics(
            ctl=round(base_ctl, 1),
            atl=round(base_atl, 1),
            tsb=round(base_tsb, 1),
            trend="stable",
        ),
        week_volume_hours=round(8.5 + (user_hash % 5) / 10, 1),
        week_load=round(45.2 + (user_hash % 10) - 5, 1),
        month_volume_hours=round(32.8 + (user_hash % 10) - 5, 1),
        month_load=round(180.5 + (user_hash % 20) - 10, 1),
        last_updated=now.isoformat(),
    )


@router.get("/distribution", response_model=TrainingDistributionResponse)
def get_training_distribution(period: str = "week", user_id: str = Depends(get_current_user)):
    """Get training distribution across zones and activity types.

    Args:
        period: Time period: week | month | season (default: week)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        TrainingDistributionResponse with distribution data
    """
    logger.info(f"[API] /training/distribution endpoint called for user_id={user_id}: period={period}")

    # Use user_id hash to make distribution user-specific
    user_hash = hash(user_id) % 1000

    # Mock zone distribution (user-specific)
    base_hours = [2.5, 3.5, 1.8, 0.5, 0.0]
    zone_hours = [round(h + (user_hash % 3) / 10 - 0.1, 1) for h in base_hours]
    total_zone_hours = sum(zone_hours)
    zone_percentages = [round((h / total_zone_hours * 100) if total_zone_hours > 0 else 0, 1) for h in zone_hours]

    zones = [
        TrainingDistributionZone(zone="Zone 1", hours=zone_hours[0], percentage=zone_percentages[0]),
        TrainingDistributionZone(zone="Zone 2", hours=zone_hours[1], percentage=zone_percentages[1]),
        TrainingDistributionZone(zone="Zone 3", hours=zone_hours[2], percentage=zone_percentages[2]),
        TrainingDistributionZone(zone="Zone 4", hours=zone_hours[3], percentage=zone_percentages[3]),
        TrainingDistributionZone(zone="Zone 5", hours=zone_hours[4], percentage=zone_percentages[4]),
    ]

    # Mock activity type distribution (user-specific)
    base_by_type = {"Run": 5.2, "Bike": 2.1, "Swim": 0.8, "Strength": 0.2}
    by_type = {k: round(v + (user_hash % 3) / 10 - 0.1, 1) for k, v in base_by_type.items()}

    total_hours = sum(z.hours for z in zones)

    return TrainingDistributionResponse(
        period=period,
        total_hours=total_hours,
        zones=zones,
        by_type=by_type,
    )


@router.get("/signals", response_model=TrainingSignalsResponse)
def get_training_signals(user_id: str = Depends(get_current_user)):
    """Get training signals and observations.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        TrainingSignalsResponse with active training signals
    """
    logger.info(f"[API] /training/signals endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    # Use user_id hash to make signals user-specific
    user_hash = hash(user_id) % 1000
    base_tsb = 7.3 + (user_hash % 5) - 2
    base_atl = 58.2 + (user_hash % 10) - 5
    base_ctl = 65.5 + (user_hash % 10) - 5

    signals = [
        TrainingSignal(
            id=f"signal_{user_id[:8]}_1",
            type="fatigue",
            severity="low",
            message="TSB is positive, indicating good recovery",
            timestamp=now.isoformat(),
            metrics={"tsb": round(base_tsb, 1), "atl": round(base_atl, 1)},
        ),
        TrainingSignal(
            id=f"signal_{user_id[:8]}_2",
            type="readiness",
            severity="moderate",
            message="CTL trending upward - maintain consistency",
            timestamp=now.isoformat(),
            metrics={"ctl": round(base_ctl, 1), "trend": 2.3},
        ),
    ]

    return TrainingSignalsResponse(
        signals=signals,
        summary="Training load is well-balanced with positive TSB indicating good recovery.",
        recommendation="Continue current training volume and intensity.",
    )
