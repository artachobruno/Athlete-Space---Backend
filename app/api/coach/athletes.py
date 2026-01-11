"""Coach athlete management API endpoints.

Provides endpoints for coaches to manage their athlete assignments.
All access is explicit - no implicit permissions.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel

from app.api.dependencies.coach import current_coach
from app.core.observe import trace
from app.core.permissions import require_coach_access
from app.db.models import Athlete, Coach, CoachAthlete
from app.db.session import get_session

router = APIRouter(prefix="/api/coach", tags=["coach"])


class AssignAthleteRequest(BaseModel):
    """Request body for assigning an athlete to a coach."""

    can_edit: bool = False


class AthleteListItem(BaseModel):
    """Response model for athlete list items."""

    athlete_id: str
    display_name: str | None
    can_edit: bool

    class Config:
        from_attributes = True


@router.get("/athletes", response_model=list[AthleteListItem])
def list_athletes(
    coach: Coach = Depends(current_coach),
) -> list[AthleteListItem]:
    """List all athletes assigned to the current coach.

    Returns:
        List of athlete assignments with permission flags
    """
    logger.info(f"Listing athletes for coach_id={coach.id}")
    start_time = time.time()

    trace_metadata: dict[str, str] = {
        "coach_id": str(coach.id),
        "endpoint": "GET /api/coach/athletes",
    }

    with trace(name="api.coach.list_athletes", metadata=trace_metadata) as span:
        with get_session() as db:
            # Query CoachAthlete join table to get assigned athletes
            links = db.query(CoachAthlete).filter_by(coach_id=coach.id).all()

            athlete_ids = [link.athlete_id for link in links]
            if not athlete_ids:
                latency_ms = int((time.time() - start_time) * 1000)
                if span and hasattr(span, "set_attribute"):
                    span.set_attribute("response_size", 0)
                    span.set_attribute("latency_ms", latency_ms)
                    span.set_attribute("athlete_count", 0)
                return []

            # Fetch athlete records
            athletes = db.query(Athlete).filter(Athlete.id.in_(athlete_ids)).all()

            # Create a map of athlete_id -> can_edit from links
            can_edit_map = {link.athlete_id: link.can_edit for link in links}

            result = [
                AthleteListItem(
                    athlete_id=athlete.id,
                    display_name=athlete.display_name,
                    can_edit=can_edit_map.get(athlete.id, False),
                )
                for athlete in athletes
            ]

            # Record metrics on span
            latency_ms = int((time.time() - start_time) * 1000)
            # Estimate response size (JSON serialization)
            response_size = len(json.dumps([item.model_dump() for item in result]))
            if span and hasattr(span, "set_attribute"):
                span.set_attribute("response_size", response_size)
                span.set_attribute("latency_ms", latency_ms)
                span.set_attribute("athlete_count", len(result))

            return result


@router.post("/athletes/{athlete_id}")
def assign_athlete(
    athlete_id: str,
    request: AssignAthleteRequest,
    coach: Coach = Depends(current_coach),
) -> dict[str, str]:
    """Assign an athlete to the current coach.

    Creates or updates the coach-athlete relationship.
    If the relationship already exists, updates the can_edit permission.

    Args:
        athlete_id: Athlete ID to assign
        request: Assignment request with can_edit permission
        coach: Current coach (from dependency)

    Returns:
        Success message

    Raises:
        HTTPException: 404 if athlete not found
        HTTPException: 409 if relationship already exists (updates it instead)
    """
    logger.info(f"Assigning athlete_id={athlete_id} to coach_id={coach.id}, can_edit={request.can_edit}")

    with get_session() as db:
        # Verify athlete exists
        athlete = db.query(Athlete).filter_by(id=athlete_id).one_or_none()
        if not athlete:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Athlete not found",
            )

        # Check if relationship already exists
        existing_link = db.query(CoachAthlete).filter_by(
            coach_id=coach.id,
            athlete_id=athlete_id,
        ).one_or_none()

        if existing_link:
            # Update existing relationship
            existing_link.can_edit = request.can_edit
            db.commit()
            logger.info(f"Updated relationship: coach_id={coach.id}, athlete_id={athlete_id}, can_edit={request.can_edit}")
            return {"message": "Athlete assignment updated"}
        else:
            # Create new relationship
            new_link = CoachAthlete(
                coach_id=coach.id,
                athlete_id=athlete_id,
                can_edit=request.can_edit,
            )
            db.add(new_link)
            db.commit()
            logger.info(f"Created relationship: coach_id={coach.id}, athlete_id={athlete_id}, can_edit={request.can_edit}")
            return {"message": "Athlete assigned successfully"}


@router.delete("/athletes/{athlete_id}")
def remove_athlete(
    athlete_id: str,
    coach: Coach = Depends(current_coach),
) -> dict[str, str]:
    """Remove an athlete assignment from the current coach.

    Args:
        athlete_id: Athlete ID to remove
        coach: Current coach (from dependency)

    Returns:
        Success message

    Raises:
        HTTPException: 404 if relationship not found
    """
    logger.info(f"Removing athlete_id={athlete_id} from coach_id={coach.id}")

    with get_session() as db:
        # Check if relationship exists
        link = db.query(CoachAthlete).filter_by(
            coach_id=coach.id,
            athlete_id=athlete_id,
        ).one_or_none()

        if not link:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Athlete assignment not found",
            )

        db.delete(link)
        db.commit()
        logger.info(f"Removed relationship: coach_id={coach.id}, athlete_id={athlete_id}")
        return {"message": "Athlete assignment removed successfully"}
