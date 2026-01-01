from fastapi import APIRouter
from loguru import logger
from sqlalchemy import select

from app.coach.chat_utils.dispatcher import dispatch_coach_chat
from app.coach.chat_utils.schemas import CoachChatRequest, CoachChatResponse
from app.state.db import get_session
from app.state.models import CoachMessage, StravaAuth

router = APIRouter(prefix="/coach", tags=["coach"])


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


@router.post("/chat", response_model=CoachChatResponse)
async def coach_chat(req: CoachChatRequest) -> CoachChatResponse:
    logger.info(f"Coach chat request: {req.message}")

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty()

    intent, reply = dispatch_coach_chat(
        message=req.message,
        days=req.days,
        days_to_race=req.days_to_race,
        history_empty=history_empty,
    )

    return CoachChatResponse(
        intent=intent,
        reply=reply,
    )
