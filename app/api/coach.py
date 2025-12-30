from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.api.state import training_load
from app.coach.chat import coach_chat
from app.coach.state_builder import build_athlete_state

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
