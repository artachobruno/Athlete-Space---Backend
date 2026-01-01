from datetime import datetime

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.api.state import training_load
from app.coach.chat import coach_chat
from app.coach.chat_utils.dispatcher import dispatch_coach_chat
from app.coach.state_builder import build_athlete_state
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
    logger.info("Coach chat request", message=req.message)

    # 1️⃣ Call function (THIS was missing)
    training_data = training_load(days=req.days)

    if not training_data.get("ctl"):
        raise HTTPException(
            status_code=404,
            detail="Insufficient training data for coaching.",
        )

    # 2️⃣ Extract latest metrics
    ctl_series = list(training_data["ctl"])
    atl_series = list(training_data["atl"])
    tsb_series = list(training_data["tsb"])

    ctl = float(ctl_series[-1])
    atl = float(atl_series[-1])
    tsb = float(tsb_series[-1])

    daily_load = [float(x) for x in training_data["daily_load"]]

    # 3️⃣ Build canonical AthleteState
    athlete_state = build_athlete_state(
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        daily_load=daily_load,
    )

    return coach_chat(
        message=req.message,
        state=athlete_state,
    )


@router.post("/query")
def ask_coach(message: str, days: int = 60, athlete_id: int = 23078584):
    """Query the coach with a message and persist conversation history."""
    logger.info("Coach query request", message=message, athlete_id=athlete_id, days=days)

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
    logger.info("Coach history requested", athlete_id=athlete_id)

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
