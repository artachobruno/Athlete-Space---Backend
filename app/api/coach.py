from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from app.api.schemas import (
    CoachAskRequest,
    CoachAskResponse,
    CoachConfidenceResponse,
    CoachObservation,
    CoachObservationsResponse,
    CoachRecommendation,
    CoachRecommendationsResponse,
    CoachSummaryResponse,
)
from app.coach.chat_utils.dispatcher import dispatch_coach_chat
from app.core.auth import get_current_user
from app.state.db import get_session
from app.state.models import CoachMessage, StravaAuth

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
# Chat endpoint
# -----------------------------
@router.post("/chat")
def chat_with_coach(req: CoachChatRequest):
    logger.info(f"Coach chat request: {req.message}")

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty()

    # Use dispatch_coach_chat which handles empty data gracefully
    try:
        intent, reply = dispatch_coach_chat(
            message=req.message,
            days=req.days,
            days_to_race=None,
            history_empty=history_empty,
        )
    except Exception as e:
        logger.error(f"Error in coach chat: {e}", exc_info=True)
        # Return a helpful message instead of raising 404
        return {
            "intent": "error",
            "reply": (
                "Sorry, I couldn't process your message. "
                "Please make sure your Strava account is connected "
                "and you have some training data synced."
            ),
        }
    else:
        return {"intent": intent, "reply": reply}


@router.post("/query")
def ask_coach(message: str, days: int = 60, athlete_id: int = 23078584):
    """Query the coach with a message and persist conversation history."""
    logger.info(f"Coach query request: message={message}, athlete_id={athlete_id}, days={days}")

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty(athlete_id=athlete_id)

    # Use dispatch_coach_chat which handles intent routing and tool execution
    intent, reply = dispatch_coach_chat(
        message=message,
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
# Phase 1 Contract Endpoints (Mock Data)
# ============================================================================


@router.get("/summary", response_model=CoachSummaryResponse)
def get_coach_summary(user_id: str = Depends(get_current_user)):
    """Get high-level coaching summary.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachSummaryResponse with training summary and focus
    """
    logger.info(f"[API] /coach/summary endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    return CoachSummaryResponse(
        summary=(
            "Your training has been consistent over the past 4 weeks with a good balance of "
            "volume and intensity. TSB is positive, indicating adequate recovery."
        ),
        current_state=(
            "Training load is well-managed with CTL at 65.5 and positive TSB of 7.3. Volume has been steady around 8-9 hours per week."
        ),
        next_focus=(
            "Maintain current training volume while gradually increasing intensity in key sessions. "
            "Focus on consistency over the next 2 weeks."
        ),
        last_updated=now.isoformat(),
    )


@router.get("/observations", response_model=CoachObservationsResponse)
def get_coach_observations(user_id: str = Depends(get_current_user)):
    """Get coaching observations.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachObservationsResponse with list of observations
    """
    logger.info(f"[API] /coach/observations endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    # Use user_id hash to make observations user-specific
    user_hash = hash(user_id) % 1000
    base_volume = 8.5 + (user_hash % 3) / 10
    base_zone2 = 42.0 + (user_hash % 5) - 2
    base_tsb = 7.3 + (user_hash % 5) - 2

    observations = [
        CoachObservation(
            id=f"obs_{user_id[:8]}_1",
            category="volume",
            observation=(
                f"Weekly volume has been consistent at {base_volume:.1f} hours "
                f"over the past 4 weeks, which is appropriate for your current fitness level."
            ),
            timestamp=now.isoformat(),
            related_metrics={"week_volume_hours": round(base_volume, 1), "avg_week_volume": round(base_volume - 0.2, 1)},
        ),
        CoachObservation(
            id=f"obs_{user_id[:8]}_2",
            category="intensity",
            observation=(
                f"Training distribution shows good balance with {base_zone2:.1f}% in Zone 2, "
                f"but could benefit from more structured high-intensity work."
            ),
            timestamp=now.isoformat(),
            related_metrics={"zone2_percentage": round(base_zone2, 1), "zone4_percentage": 6.0},
        ),
        CoachObservation(
            id=f"obs_{user_id[:8]}_3",
            category="recovery",
            observation="TSB has remained positive, indicating good recovery management and appropriate training load progression.",
            timestamp=now.isoformat(),
            related_metrics={"tsb": round(base_tsb, 1), "tsb_7d_avg": round(base_tsb - 2.1, 1)},
        ),
    ]

    return CoachObservationsResponse(
        observations=observations,
        total=len(observations),
    )


@router.get("/recommendations", response_model=CoachRecommendationsResponse)
def get_coach_recommendations(user_id: str = Depends(get_current_user)):
    """Get coaching recommendations.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachRecommendationsResponse with list of recommendations
    """
    logger.info(f"[API] /coach/recommendations endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    # Use user_id prefix to make recommendations user-specific
    user_prefix = user_id[:8]

    recommendations = [
        CoachRecommendation(
            id=f"rec_{user_prefix}_1",
            priority="medium",
            category="intensity",
            recommendation=(
                "Add one structured high-intensity session per week (intervals or tempo) to improve Zone 4 distribution from 6% to 10-15%."
            ),
            rationale=(
                "Current intensity distribution is heavily weighted toward Zone 2. "
                "Adding structured intensity will improve performance adaptations while maintaining volume."
            ),
            timestamp=now.isoformat(),
        ),
        CoachRecommendation(
            id=f"rec_{user_prefix}_2",
            priority="low",
            category="volume",
            recommendation=("Maintain current weekly volume of 8-9 hours over the next 2 weeks before considering increases."),
            rationale=(
                "Volume has been consistent and TSB is positive. "
                "Maintaining current volume allows for continued adaptation without increased injury risk."
            ),
            timestamp=now.isoformat(),
        ),
        CoachRecommendation(
            id=f"rec_{user_prefix}_3",
            priority="high",
            category="recovery",
            recommendation=("Continue monitoring TSB weekly. If TSB drops below -10, reduce volume by 20% for one week."),
            rationale=("Recovery is currently good, but proactive management prevents overreaching and maintains long-term progress."),
            timestamp=now.isoformat(),
        ),
    ]

    return CoachRecommendationsResponse(
        recommendations=recommendations,
        total=len(recommendations),
    )


@router.get("/confidence", response_model=CoachConfidenceResponse)
def get_coach_confidence(user_id: str = Depends(get_current_user)):
    """Get confidence scores for coach outputs.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachConfidenceResponse with confidence metrics
    """
    logger.info(f"[API] /coach/confidence endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)

    return CoachConfidenceResponse(
        overall=0.82,
        data_quality=0.85,
        recommendations=0.78,
        observations=0.85,
        factors=[
            "14+ days of training data available",
            "Consistent data collection",
            "Good coverage of activity types",
            "Limited high-intensity data (affects intensity recommendations)",
        ],
        last_updated=now.isoformat(),
    )


@router.post("/ask", response_model=CoachAskResponse)
def ask_coach_endpoint(request: CoachAskRequest, user_id: str = Depends(get_current_user)):
    """Ask the coach a question.

    Args:
        request: CoachAskRequest with message and optional context
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CoachAskResponse with coach's reply
    """
    logger.info(f"[API] /coach/ask endpoint called for user_id={user_id}: message={request.message}")
    now = datetime.now(timezone.utc)

    # Mock response based on message content (simple keyword matching)
    message_lower = request.message.lower()
    if "tsb" in message_lower or "recovery" in message_lower:
        reply = (
            "Your current TSB is 7.3, which is positive and indicates good recovery. "
            "This suggests you're managing your training load well and not accumulating excessive fatigue."
        )
        intent = "recovery_question"
        confidence = 0.85
    elif "volume" in message_lower or "hours" in message_lower:
        reply = (
            "Your weekly training volume is currently around 8.5 hours, "
            "which has been consistent over the past 4 weeks. "
            "This volume appears appropriate for your current fitness level and goals."
        )
        intent = "volume_question"
        confidence = 0.80
    elif "intensity" in message_lower or "zones" in message_lower:
        reply = (
            "Your training distribution shows 42% in Zone 2, 30% in Zone 1, "
            "22% in Zone 3, and 6% in Zone 4. "
            "Consider adding more structured high-intensity work to improve performance adaptations."
        )
        intent = "intensity_question"
        confidence = 0.75
    elif "next" in message_lower or "should" in message_lower or "recommend" in message_lower:
        reply = (
            "Based on your current training state, I recommend maintaining your current volume "
            "while gradually increasing structured high-intensity sessions. "
            "Focus on consistency over the next 2 weeks."
        )
        intent = "recommendation_request"
        confidence = 0.82
    else:
        reply = (
            "Thank you for your question. Based on your training data, "
            "your current training load is well-managed with positive TSB indicating good recovery. "
            "Continue maintaining consistency in your training."
        )
        intent = "general_question"
        confidence = 0.70

    return CoachAskResponse(
        reply=reply,
        intent=intent,
        confidence=confidence,
        timestamp=now.isoformat(),
    )
