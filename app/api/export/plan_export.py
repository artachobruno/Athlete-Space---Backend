"""CSV export endpoint for training plans.

Read-only endpoint that exports persisted training plan sessions to CSV format.
No LLM calls, no recomputation, no mutations - pure DB → CSV.
"""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import PlannedSession, SeasonPlan
from app.db.session import get_session

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/plans/{plan_id}/csv")
def export_plan_csv(
    plan_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Export training plan sessions to CSV.

    Args:
        plan_id: Plan ID (UUID string)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CSV file download response

    Raises:
        HTTPException: 404 if plan not found or not owned by user
    """
    logger.info(f"Exporting plan CSV for plan_id={plan_id}, user_id={user_id}")

    with get_session() as session:
        # Verify plan exists and is owned by user
        plan = session.execute(
            select(SeasonPlan).where(
                SeasonPlan.id == plan_id,
                SeasonPlan.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not plan:
            logger.warning(f"Plan not found or access denied: plan_id={plan_id}, user_id={user_id}")
            raise HTTPException(status_code=404, detail="Plan not found")

        # Get all planned sessions for this plan
        sessions = (
            session.execute(
                select(PlannedSession)
                .where(PlannedSession.plan_id == plan_id, PlannedSession.user_id == user_id)
                .order_by(PlannedSession.date)
            )
            .scalars()
            .all()
        )

        logger.info(f"Found {len(sessions)} sessions for plan_id={plan_id}")

        # Generate CSV
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "date",
                "sport",
                "title",
                "duration_minutes",
                "distance_km",
                "intensity",
                "rationale",
            ],
        )
        writer.writeheader()

        for s in sessions:
            date_value = s.date.isoformat() if s.date else ""
            sport_value = s.type or ""
            title_value = s.title or ""
            duration_value = "" if s.duration_minutes is None else s.duration_minutes
            distance_value = "" if s.distance_km is None else s.distance_km
            intensity_value = s.intensity or ""
            rationale_value = s.notes or ""

            writer.writerow({
                "date": date_value,
                "sport": sport_value,
                "title": title_value,
                "duration_minutes": duration_value,
                "distance_km": distance_value,
                "intensity": intensity_value,
                "rationale": rationale_value,
            })

        csv_content = output.getvalue()
        output.close()

        return Response(
            csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=plan_{plan_id}.csv",
            },
        )
