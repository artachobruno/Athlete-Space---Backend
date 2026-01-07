"""API endpoint for ingestion reliability metrics."""

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import StravaAccount
from app.db.session import get_session

router = APIRouter(prefix="/admin", tags=["admin"])


class IngestionReliabilityResponse(BaseModel):
    """Response containing ingestion reliability metrics."""

    total_users: int
    users_with_syncs: int
    total_syncs: int
    successful_syncs: int
    failed_syncs: int
    success_rate: float = Field(..., description="Success rate as percentage (0-100)")
    meets_target: bool = Field(..., description="Whether success rate meets >99% target")
    users_below_target: list[str] = Field(..., description="User IDs with success rate below 99%")


@router.get("/ingestion-reliability", response_model=IngestionReliabilityResponse)
def get_ingestion_reliability(user_id: str = Depends(get_current_user_id)):
    """Get ingestion reliability metrics across all users.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        IngestionReliabilityResponse with success rate metrics

    Note:
        This is an admin endpoint. In production, add admin role check.
    """
    logger.info(f"Getting ingestion reliability metrics (requested by user_id={user_id})")

    with get_session() as session:
        # Get all Strava accounts
        accounts = session.execute(select(StravaAccount)).scalars().all()

        total_users = len(accounts)
        users_with_syncs = 0
        total_syncs = 0
        successful_syncs = 0
        failed_syncs = 0
        users_below_target = []

        for account in accounts:
            success_count = account.sync_success_count or 0
            failure_count = account.sync_failure_count or 0
            user_total = success_count + failure_count

            if user_total > 0:
                users_with_syncs += 1
                total_syncs += user_total
                successful_syncs += success_count
                failed_syncs += failure_count

                # Check if user meets 99% target
                user_success_rate = (success_count / user_total) * 100 if user_total > 0 else 0
                if user_success_rate < 99.0:
                    users_below_target.append(account.user_id)

        # Calculate overall success rate
        success_rate = (successful_syncs / total_syncs * 100) if total_syncs > 0 else 0.0
        meets_target = success_rate >= 99.0

        return IngestionReliabilityResponse(
            total_users=total_users,
            users_with_syncs=users_with_syncs,
            total_syncs=total_syncs,
            successful_syncs=successful_syncs,
            failed_syncs=failed_syncs,
            success_rate=round(success_rate, 2),
            meets_target=meets_target,
            users_below_target=users_below_target,
        )
