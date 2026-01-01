from datetime import datetime

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from app.coach.chat_utils.dispatcher import dispatch_coach_chat
from app.state.db import get_session
from app.state.models import CoachMessage

router = APIRouter(prefix="/coach", tags=["coach"])


# -----------------------------
# Request schema
# -----------------------------
class CoachChatRequest(BaseModel):
    message: str
    days: int = 60


# -----------------------------
# Chat endpoint
# -----------------------------
@router.post("/chat")
def chat_with_coach(req: CoachChatRequest):
    logger.info(f"Coach chat request: {req.message}")

    # Use dispatch_coach_chat which handles empty data gracefully
    try:
        intent, reply = dispatch_coach_chat(
            message=req.message,
            days=req.days,
            days_to_race=None,
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

    # Use dispatch_coach_chat which handles intent routing and tool execution
    intent, reply = dispatch_coach_chat(
        message=message,
        days=days,
        days_to_race=None,
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
