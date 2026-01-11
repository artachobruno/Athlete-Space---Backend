"""Manual upload endpoints for training sessions, weeks, and seasons.

Phase 2 & 3: Fallback endpoints for manual uploads (non-chat).
"""

import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.coach.tools.session_planner import save_sessions_to_database
from app.core.observe import trace
from app.db.models import PlannedSession, StravaAccount
from app.db.session import get_session
from app.upload.plan_handler import upload_plan_from_chat
from app.upload.plan_parser import ParsedSessionUpload, parse_csv_plan, parse_text_plan

router = APIRouter(prefix="/training", tags=["training", "upload"])

# Guardrails
MAX_CONTENT_SIZE = 1 * 1024 * 1024  # 1MB for text/CSV content
MAX_SESSIONS_PER_REQUEST = 500


def _validate_production_auth(user_id: str) -> None:
    """Validate authentication in production environment.

    Args:
        user_id: User ID from auth dependency (already validated by get_current_user_id)

    Note:
        This is a redundant check since get_current_user_id already validates auth.
        Kept for explicit guardrail documentation.
    """
    env = os.getenv("APP_ENV", "local")
    if env == "production" and not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required in production",
        )


def _raise_duplicate_session_error() -> None:
    """Raise HTTPException for duplicate session error."""
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Session already exists (duplicate detected)",
    )


def _save_week_sessions_and_get_ids(
    session_dicts: list[dict],
    user_id: str,
    athlete_id: int,
) -> list[str]:
    """Save week sessions to database and return their IDs.

    Args:
        session_dicts: List of session dictionaries to save
        user_id: User ID
        athlete_id: Athlete ID

    Returns:
        List of session IDs (skips duplicates)
    """
    session_ids: list[str] = []
    with get_session() as session:
        for session_dict in session_dicts:
            # Check for duplicate
            existing = session.execute(
                select(PlannedSession)
                .where(PlannedSession.user_id == user_id)
                .where(PlannedSession.date == session_dict["date"])
                .where(PlannedSession.title == session_dict["title"])
            ).first()

            if existing:
                continue  # Skip duplicate

            # Create session
            planned_session = PlannedSession(
                user_id=user_id,
                athlete_id=athlete_id,
                date=session_dict["date"],
                time=session_dict.get("time"),
                type=session_dict["type"],
                title=session_dict["title"],
                duration_minutes=session_dict.get("duration_minutes"),
                distance_km=session_dict.get("distance_km"),
                intensity=session_dict.get("intensity"),
                notes=session_dict.get("notes"),
                plan_type="manual_upload",
                plan_id=None,
                status="planned",
                completed=False,
            )

            session.add(planned_session)
            session_ids.append("pending")  # Will update after commit

        session.commit()

        # Refresh to get IDs
        for i, session_dict in enumerate(session_dicts):
            if i < len(session_ids) and session_ids[i] == "pending":
                result = session.execute(
                    select(PlannedSession)
                    .where(PlannedSession.user_id == user_id)
                    .where(PlannedSession.date == session_dict["date"])
                    .where(PlannedSession.title == session_dict["title"])
                    .order_by(PlannedSession.created_at.desc())
                    .limit(1)
                ).first()
                if result:
                    session_ids[i] = result[0].id

    return session_ids


def _get_athlete_id_from_user_id(user_id: str) -> int:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        user_id: User ID (Clerk)

    Returns:
        Athlete ID (Strava)

    Raises:
        HTTPException: If Strava account not found
    """
    with get_session() as session:
        result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Strava account not connected. Please connect your Strava account first.",
            )
        return int(result[0].athlete_id)


class ManualSessionRequest(BaseModel):
    """Request model for manual session upload."""

    date: datetime = Field(..., description="Session date and time (timezone-aware)")
    time: str | None = Field(default=None, description="Session time (HH:MM format)")
    type: str = Field(..., description="Activity type (Run, Bike, Swim, etc.)")
    title: str = Field(..., description="Session title")
    duration_minutes: int | None = Field(default=None, description="Duration in minutes")
    distance_km: float | None = Field(default=None, description="Distance in kilometers")
    intensity: str | None = Field(default=None, description="Intensity (easy, moderate, hard, race)")
    notes: str | None = Field(default=None, description="Optional notes")


class ManualSessionResponse(BaseModel):
    """Response model for manual session upload."""

    session_id: str
    message: str


class ManualWeekRequest(BaseModel):
    """Request model for manual week upload."""

    sessions: list[ManualSessionRequest] = Field(..., description="List of sessions for the week", max_length=MAX_SESSIONS_PER_REQUEST)
    week_start: datetime | None = Field(default=None, description="Week start date (for validation)")


class ManualWeekResponse(BaseModel):
    """Response model for manual week upload."""

    session_count: int
    session_ids: list[str]
    message: str


class ManualSeasonRequest(BaseModel):
    """Request model for manual season upload."""

    weeks: list[list[ManualSessionRequest]] = Field(..., description="List of weeks, each containing sessions", max_length=52)
    season_start: datetime | None = Field(default=None, description="Season start date (for validation)")


class ManualSeasonResponse(BaseModel):
    """Response model for manual season upload."""

    week_count: int
    session_count: int
    session_ids: list[str]
    message: str


@router.post("/sessions/manual", response_model=ManualSessionResponse, status_code=status.HTTP_201_CREATED)
async def upload_manual_session(
    request: ManualSessionRequest,
    user_id: str = Depends(get_current_user_id),
) -> ManualSessionResponse:
    """Upload a single training session manually (non-chat).

    Creates a planned session that appears on the calendar.

    Args:
        request: Session data
        user_id: Current authenticated user ID

    Returns:
        ManualSessionResponse with session_id

    Raises:
        HTTPException: 400 if validation fails, 404 if Strava account not found, 500 on error
    """
    trace_meta = {
        "user_id": user_id,
        "endpoint": "upload_manual_session",
        "session_type": request.type,
        "session_date": request.date.isoformat(),
    }

    with trace(name="api.upload_manual_session", metadata=trace_meta):
        # Guardrail: Production auth check
        _validate_production_auth(user_id)

        logger.info(
            "Manual session upload request",
            user_id=user_id,
            session_type=request.type,
            session_date=request.date.isoformat(),
        )

        # Get athlete_id
        athlete_id = _get_athlete_id_from_user_id(user_id)

        # Validate minimal fields
        if not request.type or not request.title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Type and title are required fields",
            )

        # Save session and get ID
        try:
            with get_session() as session:
                # Check for duplicate
                existing = session.execute(
                    select(PlannedSession)
                    .where(PlannedSession.user_id == user_id)
                    .where(PlannedSession.date == request.date)
                    .where(PlannedSession.title == request.title)
                ).first()

                if existing:
                    _raise_duplicate_session_error()

                # Create session (all required fields from PlannedSession model)
                planned_session = PlannedSession(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    date=request.date,
                    time=request.time,
                    type=request.type,
                    title=request.title,
                    duration_minutes=request.duration_minutes,
                    distance_km=request.distance_km,
                    intensity=request.intensity,
                    notes=request.notes,
                    plan_type="manual_upload",
                    plan_id=None,
                    week_number=None,
                    status="planned",
                    completed=False,
                )

                session.add(planned_session)
                session.commit()
                session.refresh(planned_session)

                session_id = planned_session.id

            logger.info(
                "Manual session uploaded successfully",
                user_id=user_id,
                athlete_id=athlete_id,
                session_id=session_id,
            )

            return ManualSessionResponse(
                session_id=session_id,
                message=f"Session '{request.title}' uploaded successfully",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error uploading manual session: {e}", exc_info=True, user_id=user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload session",
            ) from e


@router.post("/weeks/manual", response_model=ManualWeekResponse, status_code=status.HTTP_201_CREATED)
async def upload_manual_week(
    request: ManualWeekRequest,
    user_id: str = Depends(get_current_user_id),
) -> ManualWeekResponse:
    """Upload an entire training week manually (non-chat).

    Creates multiple planned sessions atomically.

    Args:
        request: Week data with list of sessions
        user_id: Current authenticated user ID

    Returns:
        ManualWeekResponse with session count and IDs

    Raises:
        HTTPException: 400 if validation fails, 404 if Strava account not found, 500 on error
    """
    trace_meta = {
        "user_id": user_id,
        "endpoint": "upload_manual_week",
        "session_count": len(request.sessions),
    }

    with trace(name="api.upload_manual_week", metadata=trace_meta):
        # Guardrail: Production auth check
        _validate_production_auth(user_id)

        logger.info(
            "Manual week upload request",
            user_id=user_id,
            session_count=len(request.sessions),
        )

        # Guardrail: Validate content size
        if len(request.sessions) > MAX_SESSIONS_PER_REQUEST:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Too many sessions. Maximum {MAX_SESSIONS_PER_REQUEST} sessions per request",
            )

        # Get athlete_id
        athlete_id = _get_athlete_id_from_user_id(user_id)

        # Convert to session dictionaries
        session_dicts: list[dict] = []
        for session_req in request.sessions:
            session_dict = {
                "date": session_req.date,
                "time": session_req.time,
                "type": session_req.type,
                "title": session_req.title,
                "duration_minutes": session_req.duration_minutes,
                "distance_km": session_req.distance_km,
                "intensity": session_req.intensity,
                "notes": session_req.notes,
            }
            session_dicts.append(session_dict)

        # Validate date coverage and overlaps
        dates = [s["date"] for s in session_dicts]
        if len(set(dates)) < len(dates):
            logger.warning("Duplicate dates detected in week upload", user_id=user_id)

        # Save sessions atomically using existing function
        try:
            saved_count = save_sessions_to_database(
                user_id=user_id,
                athlete_id=athlete_id,
                sessions=session_dicts,
                plan_type="manual_upload",
                plan_id=None,
            )

            # Get created session IDs (query by date range)
            session_ids: list[str] = []
            if saved_count > 0:
                with get_session() as session:
                    dates = [s["date"] for s in session_dicts]
                    min_date = min(dates)
                    max_date = max(dates)

                    results = session.execute(
                        select(PlannedSession)
                        .where(PlannedSession.user_id == user_id)
                        .where(PlannedSession.date >= min_date)
                        .where(PlannedSession.date <= max_date)
                        .where(PlannedSession.plan_type == "manual_upload")
                        .order_by(PlannedSession.created_at.desc())
                        .limit(saved_count)
                    ).all()

                    session_ids = [row[0].id for row in results[:saved_count]]

            logger.info(
                "Manual week uploaded successfully",
                user_id=user_id,
                athlete_id=athlete_id,
                saved_count=saved_count,
                requested_count=len(request.sessions),
            )

            return ManualWeekResponse(
                session_count=saved_count,
                session_ids=[sid for sid in session_ids if sid != "pending"],
                message=f"Uploaded {saved_count} sessions for the week",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error uploading manual week: {e}", exc_info=True, user_id=user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload week",
            ) from e


@router.post("/seasons/manual", response_model=ManualSeasonResponse, status_code=status.HTTP_201_CREATED)
async def upload_manual_season(
    request: ManualSeasonRequest,
    user_id: str = Depends(get_current_user_id),
) -> ManualSeasonResponse:
    """Upload a full training season manually (non-chat).

    Creates multiple weeks of planned sessions with deterministic expansion.

    Args:
        request: Season data with list of weeks (each containing sessions)
        user_id: Current authenticated user ID

    Returns:
        ManualSeasonResponse with week count, session count, and IDs

    Raises:
        HTTPException: 400 if validation fails, 404 if Strava account not found, 500 on error
    """
    trace_meta = {
        "user_id": user_id,
        "endpoint": "upload_manual_season",
        "week_count": len(request.weeks),
    }

    with trace(name="api.upload_manual_season", metadata=trace_meta):
        # Guardrail: Production auth check
        _validate_production_auth(user_id)

        logger.info(
            "Manual season upload request",
            user_id=user_id,
            week_count=len(request.weeks),
        )

        # Guardrail: Validate content size
        total_sessions = sum(len(week) for week in request.weeks)
        if total_sessions > MAX_SESSIONS_PER_REQUEST * 52:  # Reasonable limit for a season
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Too many sessions. Maximum {MAX_SESSIONS_PER_REQUEST * 52} sessions per season",
            )

        # Get athlete_id
        athlete_id = _get_athlete_id_from_user_id(user_id)

        # Expand weeks to sessions with deterministic ordering
        all_sessions: list[dict] = []
        for week_num, week_sessions in enumerate(request.weeks, start=1):
            for session_req in week_sessions:
                session_dict = {
                    "date": session_req.date,
                    "time": session_req.time,
                    "type": session_req.type,
                    "title": session_req.title,
                    "duration_minutes": session_req.duration_minutes,
                    "distance_km": session_req.distance_km,
                    "intensity": session_req.intensity,
                    "notes": session_req.notes,
                    "week_number": week_num,  # Add week number for season context
                }
                all_sessions.append(session_dict)

        # Sort sessions by date for deterministic ordering
        all_sessions.sort(key=lambda s: s["date"])

        # Validate date coverage
        if not all_sessions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Season must contain at least one session",
            )

        # Save sessions atomically using existing function
        try:
            saved_count = save_sessions_to_database(
                user_id=user_id,
                athlete_id=athlete_id,
                sessions=all_sessions,
                plan_type="manual_upload",
                plan_id=None,
            )

            # Get created session IDs (query by date range)
            session_ids: list[str] = []
            if saved_count > 0:
                dates = [s["date"] for s in all_sessions]
                min_date = min(dates)
                max_date = max(dates)

                with get_session() as session:
                    results = session.execute(
                        select(PlannedSession)
                        .where(PlannedSession.user_id == user_id)
                        .where(PlannedSession.date >= min_date)
                        .where(PlannedSession.date <= max_date)
                        .where(PlannedSession.plan_type == "manual_upload")
                        .order_by(PlannedSession.created_at.desc())
                        .limit(saved_count)
                    ).all()

                    session_ids = [row[0].id for row in results[:saved_count]]

            logger.info(
                "Manual season uploaded successfully",
                user_id=user_id,
                athlete_id=athlete_id,
                saved_count=saved_count,
                week_count=len(request.weeks),
                total_requested=total_sessions,
            )

            return ManualSeasonResponse(
                week_count=len(request.weeks),
                session_count=saved_count,
                session_ids=session_ids,
                message=f"Uploaded {saved_count} sessions across {len(request.weeks)} weeks",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error uploading manual season: {e}", exc_info=True, user_id=user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload season",
            ) from e
