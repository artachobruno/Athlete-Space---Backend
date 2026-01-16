"""API endpoint for exposing risk flags in user-friendly language."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import Activity, StravaAccount
from app.db.session import get_session
from app.state.builder import build_training_state
from app.state.models import ActivityRecord

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class RiskFlag(BaseModel):
    """User-friendly risk flag representation."""

    type: Literal["fatigue", "monotony", "load_spike", "recovery"]
    severity: Literal["low", "medium", "high"]
    message: str = Field(..., description="User-friendly explanation in non-clinical language")
    recommendation: str = Field(..., description="Conservative recommendation")


class RiskFlagsResponse(BaseModel):
    """Response containing risk flags."""

    flags: list[RiskFlag]
    overall_risk: Literal["none", "low", "medium", "high"]
    summary: str


def _get_athlete_id_from_user(user_id: str) -> int:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        user_id: Current authenticated user ID

    Returns:
        Athlete ID as integer

    Raises:
        HTTPException: If Strava account not found
    """
    with get_session() as session:
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Strava account not connected",
            )
        return int(account[0].athlete_id)


def _convert_risk_flags_to_user_friendly(risk_flags: Sequence[str], training_state: dict) -> list[RiskFlag]:
    """Convert internal risk flags to user-friendly messages.

    Args:
        risk_flags: List of internal risk flag codes
        training_state: Training state dictionary

    Returns:
        List of user-friendly RiskFlag objects
    """
    flags = []
    tsb = training_state.get("tsb", 0.0)
    monotony = training_state.get("monotony", 0.0)

    for flag in risk_flags:
        if flag == "OVERREACHING":
            flags.append(
                RiskFlag(
                    type="fatigue",
                    severity="high",
                    message="Training load is elevated. Recovery should be prioritized.",
                    recommendation="Consider reducing volume and intensity for a few days to allow recovery.",
                )
            )
        elif flag == "HIGH_MONOTONY":
            flags.append(
                RiskFlag(
                    type="monotony",
                    severity="medium",
                    message="Training pattern is very repetitive. Variety can help reduce injury risk.",
                    recommendation="Consider varying your training routes, intensities, or activities.",
                )
            )
        elif flag == "ACUTE_SPIKE":
            flags.append(
                RiskFlag(
                    type="load_spike",
                    severity="high",
                    message="Recent training load increased sharply. This can increase injury risk.",
                    recommendation="Gradually reduce load and prioritize easy recovery work.",
                )
            )
        elif flag == "INSUFFICIENT_RECOVERY":
            flags.append(
                RiskFlag(
                    type="recovery",
                    severity="medium",
                    message="Recovery may be insufficient. Fatigue is accumulating.",
                    recommendation="Add more rest days or reduce training intensity.",
                )
            )

    # Add TSB-based flags if not already covered
    if not any(f.type == "fatigue" for f in flags):
        if tsb < -15:
            flags.append(
                RiskFlag(
                    type="fatigue",
                    severity="high",
                    message="Fatigue is very high. Recovery is needed.",
                    recommendation="Take a rest day or reduce training significantly.",
                )
            )
        elif tsb < -10:
            flags.append(
                RiskFlag(
                    type="fatigue",
                    severity="medium",
                    message="Fatigue is elevated. Monitor recovery closely.",
                    recommendation="Prioritize easy training and ensure adequate rest.",
                )
            )

    # Add monotony flag if high but not already flagged
    if monotony >= 2.0 and not any(f.type == "monotony" for f in flags):
        flags.append(
            RiskFlag(
                type="monotony",
                severity="low",
                message="Training pattern is somewhat repetitive.",
                recommendation="Consider adding variety to your training routine.",
            )
        )

    return flags


@router.get("/risks", response_model=RiskFlagsResponse)
def get_risk_flags(user_id: str = Depends(get_current_user_id)):
    """Get user-friendly risk flags for the current user.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        RiskFlagsResponse with risk flags in non-clinical language

    Raises:
        HTTPException: If Strava account not connected or data unavailable
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    logger.info(f"Getting risk flags for user_id={user_id}, athlete_id={athlete_id}")

    # Get activities for state building
    with get_session() as session:
        since = datetime.now(timezone.utc) - timedelta(days=28)
        activities = (
            session.execute(
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.starts_at >= since,
                )
                .order_by(Activity.starts_at.desc())
            )
            .scalars()
            .all()
        )

        # Convert to ActivityRecord format
        activity_records = [
            ActivityRecord(
                athlete_id=athlete_id,
                activity_id=str(act.id),
                source="strava",
                sport=act.type or "unknown",
                start_time=act.start_time,
                duration_sec=act.duration_seconds or 0,
                distance_m=act.distance_meters or 0.0,
                elevation_m=act.elevation_gain_meters or 0.0,
                avg_hr=None,
                power=None,
            )
            for act in activities
        ]

    # Build training state
    today = datetime.now(timezone.utc).date()
    training_state_obj = build_training_state(activities=activity_records, today=today)

    # Convert to dict for easier access
    training_state_dict = {
        "tsb": training_state_obj.training_stress_balance,
        "monotony": training_state_obj.monotony,
        "acute_load": training_state_obj.acute_load_7d,
        "chronic_load": training_state_obj.chronic_load_28d,
    }

    # Convert risk flags to user-friendly format
    risk_flags = _convert_risk_flags_to_user_friendly(training_state_obj.risk_flags, training_state_dict)

    # Determine overall risk
    if any(f.severity == "high" for f in risk_flags):
        overall_risk = "high"
    elif any(f.severity == "medium" for f in risk_flags):
        overall_risk = "medium"
    elif risk_flags:
        overall_risk = "low"
    else:
        overall_risk = "none"

    # Generate summary
    if overall_risk == "none":
        summary = "No significant risk factors detected. Continue training as planned."
    elif overall_risk == "low":
        summary = "Minor risk factors present. Monitor training and recovery."
    elif overall_risk == "medium":
        summary = "Some risk factors detected. Consider adjusting training load."
    else:
        summary = "Significant risk factors present. Prioritize recovery and reduce training load."

    return RiskFlagsResponse(
        flags=risk_flags,
        overall_risk=overall_risk,
        summary=summary,
    )
