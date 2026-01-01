from fastapi import APIRouter
from loguru import logger

from app.coach.chat_utils.dispatcher import dispatch_coach_chat
from app.coach.chat_utils.schemas import CoachChatRequest, CoachChatResponse

router = APIRouter(prefix="/coach", tags=["coach"])


@router.post("/chat", response_model=CoachChatResponse)
async def coach_chat(req: CoachChatRequest) -> CoachChatResponse:
    logger.info(f"Coach chat request: {req.message}")

    intent, reply = dispatch_coach_chat(
        message=req.message,
        days=req.days,
        days_to_race=req.days_to_race,
    )

    return CoachChatResponse(
        intent=intent,
        reply=reply,
    )
