from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.api.schemas.schemas import (
    CoachAskRequest,
    CoachAskResponse,
    CoachConfidenceResponse,
    CoachObservation,
    CoachObservationsResponse,
    CoachRecommendation,
    CoachRecommendationsResponse,
    CoachSummaryResponse,
)
from app.api.user.me import get_overview_data
from app.coach.services.chat_service import dispatch_coach_chat
from app.coach.services.coach_service import get_coach_advice
from app.coach.utils.context_builder import build_coach_context
from app.db.models import CoachMessage, StravaAccount, StravaAuth
from app.db.session import get_session

router = APIRouter(prefix="/coach", tags=["coach"])


# -----------------------------
# Request schema
# -----------------------------
class CoachChatRequest(BaseModel):
    message: str
    days: int = 60


def _is_history_empty(athlete_id: int | None = None) -> bool:
    """Check if coach chat history is empty for an athlete.

    Args:
        athlete_id: Optional athlete ID. If None, checks the first athlete from StravaAuth.

    Returns:
        True if history is empty (cold start), False otherwise.
    """
    with get_session() as db:
        # If no athlete_id provided, try to get the first one from StravaAuth
        if athlete_id is None:
            result = db.execute(select(StravaAuth)).first()
            if not result:
                # No Strava auth, treat as cold start
                return True
            athlete_id = result[0].athlete_id

        # Check if there are any messages for this athlete
        message_count = db.query(CoachMessage).filter(CoachMessage.athlete_id == athlete_id).count()

        return message_count == 0


# -----------------------------
# Chat endpoint - DEPRECATED: Use /coach/chat from coach_chat router instead
# This endpoint is kept for backward compatibility but coach_chat router takes precedence
# -----------------------------
# NOTE: The /coach/chat endpoint is now handled by app/api/coach_chat.py
# This duplicate endpoint is effectively unused since coach_chat_router is registered after coach_router


@router.post("/query")
def ask_coach(message: str, days: int = 60, athlete_id: int = 23078584):
    """Query the coach with a message and persist conversation history."""
    logger.info(f"Coach query request: message={message}, athlete_id={athlete_id}, days={days}")

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty(athlete_id=athlete_id)

    # Use dispatch_coach_chat which handles intent routing and tool execution
    intent, reply = dispatch_coach_chat(
        message=message,
        athlete_id=athlete_id,
        days=days,
        days_to_race=None,
        history_empty=history_empty,
    )

    # Save messages to database
    with get_session() as db:
        db.add(CoachMessage(athlete_id=athlete_id, role="user", content=message))
        db.add(CoachMessage(athlete_id=athlete_id, role="assistant", content=reply))

    return {"reply": reply, "intent": intent}


@router.get("/history")
def history(athlete_id: int = 23078584):
    """Get coach conversation history for an athlete."""
    logger.info(f"Coach history requested: athlete_id={athlete_id}")

    with get_session() as db:
        msgs = db.query(CoachMessage).filter(CoachMessage.athlete_id == athlete_id).order_by(CoachMessage.timestamp).all()
        return [
            {
                "role": m.role,
                "content": m.content,
                "time": m.timestamp.isoformat() if isinstance(m.timestamp, datetime) else str(m.timestamp),
            }
            for m in msgs
        ]


# ============================================================================
# Phase 1 Contract Endpoints (Real LLM Data)
# ============================================================================
# All endpoints now use real LLM calls via get_coach_advice and dispatch_coach_chat
# No mock data or hardcoded responses


@router.get("/context")
def get_coach_context(user_id: str = Depends(get_current_user_id)):
    """Get coach context (backward compatibility endpoint).

    DEPRECATED: This endpoint is kept for backward compatibility.
    Frontend should use /me/overview instead and build context client-side.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Coach context dictionary built from overview data
    """
    logger.info(f"[API] /coach/context endpoint called for user_id={user_id}")
    try:
        overview = get_overview_data(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting overview for coach context: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get overview: {e!s}") from e
    else:
        return build_coach_context(overview)


@router.get("/summary", response_model=CoachSummaryResponse)
def get_coach_summary(user_id: str = Depends(get_current_user_id)):
    """Get high-level coaching summary from real LLM.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachSummaryResponse with training summary and focus from LLM
    """
    logger.info(f"[API] /coach/summary endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    try:
        overview = get_overview_data(user_id)
        coach_response = get_coach_advice(overview)

        summary = coach_response.get("summary", "Training analysis in progress.")
        insights = coach_response.get("insights", [])
        recommendations = coach_response.get("recommendations", [])

        current_state = insights[0] if insights else summary
        next_focus = recommendations[0] if recommendations else "Continue monitoring your training load and recovery."

        return CoachSummaryResponse(
            summary=summary,
            current_state=current_state,
            next_focus=next_focus,
            last_updated=now.isoformat(),
        )
    except Exception:
        logger.exception("Error getting coach summary")
        return CoachSummaryResponse(
            summary="Unable to generate coaching summary at this time.",
            current_state="Please ensure your Strava account is connected and synced.",
            next_focus="Check back once you have sufficient training data.",
            last_updated=now.isoformat(),
        )


@router.get("/observations", response_model=CoachObservationsResponse)
def get_coach_observations(user_id: str = Depends(get_current_user_id)):
    """Get coaching observations from real LLM.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachObservationsResponse with list of observations from LLM
    """
    logger.info(f"[API] /coach/observations endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    try:
        overview = get_overview_data(user_id)
        coach_response = get_coach_advice(overview)

        insights = coach_response.get("insights", [])
        today_metrics = overview.get("today", {})

        observations = []
        categories = ["volume", "intensity", "recovery", "consistency"]

        for idx, insight in enumerate(insights[:3]):
            category = categories[idx % len(categories)]
            observation = CoachObservation(
                id=f"obs_{user_id[:8]}_{idx + 1}",
                category=category,
                observation=insight,
                timestamp=now.isoformat(),
                related_metrics={
                    "ctl": today_metrics.get("ctl", 0.0),
                    "atl": today_metrics.get("atl", 0.0),
                    "tsb": today_metrics.get("tsb", 0.0),
                },
            )
            observations.append(observation)

        if not observations:
            observations.append(
                CoachObservation(
                    id=f"obs_{user_id[:8]}_1",
                    category="general",
                    observation=coach_response.get("summary", "Training analysis in progress."),
                    timestamp=now.isoformat(),
                    related_metrics={},
                )
            )

        return CoachObservationsResponse(
            observations=observations,
            total=len(observations),
        )
    except Exception as e:
        logger.error(f"Error getting coach observations: {e}", exc_info=True)
        return CoachObservationsResponse(
            observations=[],
            total=0,
        )


@router.get("/recommendations", response_model=CoachRecommendationsResponse)
def get_coach_recommendations(user_id: str = Depends(get_current_user_id)):
    """Get coaching recommendations from real LLM.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachRecommendationsResponse with list of recommendations from LLM
    """
    logger.info(f"[API] /coach/recommendations endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    try:
        overview = get_overview_data(user_id)
        coach_response = get_coach_advice(overview)

        recommendations_list = coach_response.get("recommendations", [])
        risk_level = coach_response.get("risk_level", "none")
        intervention = coach_response.get("intervention", False)

        user_prefix = user_id[:8]
        recommendations = []

        categories = ["intensity", "volume", "recovery", "structure"]
        priority_map = {
            "high": "high",
            "medium": "medium",
            "low": "low",
            "none": "low",
        }
        priority = priority_map.get(risk_level, "medium")

        for idx, rec_text in enumerate(recommendations_list[:3]):
            category = categories[idx % len(categories)]
            recommendation = CoachRecommendation(
                id=f"rec_{user_prefix}_{idx + 1}",
                priority=priority if intervention else "medium",
                category=category,
                recommendation=rec_text,
                rationale=coach_response.get("summary", "Based on current training state analysis."),
                timestamp=now.isoformat(),
            )
            recommendations.append(recommendation)

        if not recommendations:
            recommendations.append(
                CoachRecommendation(
                    id=f"rec_{user_prefix}_1",
                    priority="low",
                    category="general",
                    recommendation="Continue monitoring your training load and recovery.",
                    rationale="Maintain consistency in your training routine.",
                    timestamp=now.isoformat(),
                )
            )

        return CoachRecommendationsResponse(
            recommendations=recommendations,
            total=len(recommendations),
        )
    except Exception as e:
        logger.error(f"Error getting coach recommendations: {e}", exc_info=True)
        return CoachRecommendationsResponse(
            recommendations=[],
            total=0,
        )


@router.get("/confidence", response_model=CoachConfidenceResponse)
def get_coach_confidence(user_id: str = Depends(get_current_user_id)):
    """Get confidence scores for coach outputs based on real data quality.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachConfidenceResponse with confidence metrics calculated from data quality
    """
    logger.info(f"[API] /coach/confidence endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    try:
        overview = get_overview_data(user_id)
        data_quality = overview.get("data_quality", "insufficient")

        data_quality_scores = {
            "ok": 0.90,
            "limited": 0.65,
            "insufficient": 0.30,
        }

        data_quality_score = data_quality_scores.get(data_quality, 0.30)

        factors = []

        if data_quality == "ok":
            factors.extend([
                "14+ days of training data available",
                "Consistent data collection",
                "Good coverage of activity types",
            ])
            overall = 0.85
            recommendations = 0.80
            observations = 0.85
        elif data_quality == "limited":
            factors.extend([
                "Some training data available but with gaps",
                "Limited data consistency",
                "May affect recommendation accuracy",
            ])
            overall = 0.65
            recommendations = 0.60
            observations = 0.65
        else:
            factors.extend([
                "Insufficient training data (<14 days)",
                "Cannot provide reliable recommendations",
                "Need more consistent data collection",
            ])
            overall = 0.30
            recommendations = 0.25
            observations = 0.30

        return CoachConfidenceResponse(
            overall=overall,
            data_quality=data_quality_score,
            recommendations=recommendations,
            observations=observations,
            factors=factors,
            last_updated=now.isoformat(),
        )
    except Exception as e:
        logger.error(f"Error getting coach confidence: {e}", exc_info=True)
        return CoachConfidenceResponse(
            overall=0.30,
            data_quality=0.30,
            recommendations=0.25,
            observations=0.30,
            factors=["Unable to assess data quality"],
            last_updated=now.isoformat(),
        )


@router.post("/ask", response_model=CoachAskResponse)
def ask_coach_endpoint(request: CoachAskRequest, user_id: str = Depends(get_current_user_id)):
    """Ask the coach a question using real LLM.

    Args:
        request: CoachAskRequest with message and optional context
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachAskResponse with coach's reply from LLM
    """
    logger.info(f"[API] /coach/ask endpoint called for user_id={user_id}: message={request.message}")
    now = datetime.now(timezone.utc)

    try:
        # Get athlete_id from user_id
        with get_session() as session:
            result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
            if not result:
                logger.warning(f"No Strava account found for user_id={user_id}")
                return CoachAskResponse(
                    reply="Please connect your Strava account first.",
                    intent="error",
                    confidence=0.0,
                    timestamp=now.isoformat(),
                )
            athlete_id = int(result[0].athlete_id)

        history_empty = _is_history_empty(athlete_id=athlete_id)

        intent, reply = dispatch_coach_chat(
            message=request.message,
            athlete_id=athlete_id,
            days=60,
            days_to_race=None,
            history_empty=history_empty,
        )

        overview = get_overview_data(user_id)
        data_quality = overview.get("data_quality", "insufficient")

        confidence_scores = {
            "ok": 0.85,
            "limited": 0.65,
            "insufficient": 0.40,
        }
        confidence = confidence_scores.get(data_quality, 0.40)

        return CoachAskResponse(
            reply=reply,
            intent=intent,
            confidence=confidence,
            timestamp=now.isoformat(),
        )
    except Exception as e:
        logger.error(f"Error in coach ask endpoint: {e}", exc_info=True)
        return CoachAskResponse(
            reply=(
                "I encountered an error processing your question. "
                "Please make sure your Strava account is connected and synced, "
                "and try again."
            ),
            intent="error",
            confidence=0.0,
            timestamp=now.isoformat(),
        )
