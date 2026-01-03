"""Training API endpoints - Phase 1: Mock data implementation.

These endpoints return mock data to establish the API contract before
implementing real data logic.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from loguru import logger

from app.api.schemas import (
    TrainingDistributionResponse,
    TrainingDistributionZone,
    TrainingSignal,
    TrainingSignalsResponse,
    TrainingStateMetrics,
    TrainingStateResponse,
)

router = APIRouter(prefix="/training", tags=["training"])


@router.get("/state", response_model=TrainingStateResponse)
def get_training_state():
    """Get current training state and metrics.

    Returns:
        TrainingStateResponse with current training state
    """
    logger.info("[API] /training/state endpoint called")
    now = datetime.now(timezone.utc)

    return TrainingStateResponse(
        current=TrainingStateMetrics(
            ctl=65.5,
            atl=58.2,
            tsb=7.3,
            trend="stable",
        ),
        week_volume_hours=8.5,
        week_load=45.2,
        month_volume_hours=32.8,
        month_load=180.5,
        last_updated=now.isoformat(),
    )


@router.get("/distribution", response_model=TrainingDistributionResponse)
def get_training_distribution(period: str = "week"):
    """Get training distribution across zones and activity types.

    Args:
        period: Time period: week | month | season (default: week)

    Returns:
        TrainingDistributionResponse with distribution data
    """
    logger.info(f"[API] /training/distribution endpoint called: period={period}")

    # Mock zone distribution
    zones = [
        TrainingDistributionZone(zone="Zone 1", hours=2.5, percentage=30.0),
        TrainingDistributionZone(zone="Zone 2", hours=3.5, percentage=42.0),
        TrainingDistributionZone(zone="Zone 3", hours=1.8, percentage=22.0),
        TrainingDistributionZone(zone="Zone 4", hours=0.5, percentage=6.0),
        TrainingDistributionZone(zone="Zone 5", hours=0.0, percentage=0.0),
    ]

    # Mock activity type distribution
    by_type = {
        "Run": 5.2,
        "Bike": 2.1,
        "Swim": 0.8,
        "Strength": 0.2,
    }

    total_hours = sum(z.hours for z in zones)

    return TrainingDistributionResponse(
        period=period,
        total_hours=total_hours,
        zones=zones,
        by_type=by_type,
    )


@router.get("/signals", response_model=TrainingSignalsResponse)
def get_training_signals():
    """Get training signals and observations.

    Returns:
        TrainingSignalsResponse with active training signals
    """
    logger.info("[API] /training/signals endpoint called")
    now = datetime.now(timezone.utc)

    signals = [
        TrainingSignal(
            id="signal_1",
            type="fatigue",
            severity="low",
            message="TSB is positive, indicating good recovery",
            timestamp=now.isoformat(),
            metrics={"tsb": 7.3, "atl": 58.2},
        ),
        TrainingSignal(
            id="signal_2",
            type="readiness",
            severity="moderate",
            message="CTL trending upward - maintain consistency",
            timestamp=now.isoformat(),
            metrics={"ctl": 65.5, "trend": 2.3},
        ),
    ]

    return TrainingSignalsResponse(
        signals=signals,
        summary="Training load is well-balanced with positive TSB indicating good recovery.",
        recommendation="Continue current training volume and intensity.",
    )
