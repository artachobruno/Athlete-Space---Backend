from fastapi import APIRouter
from loguru import logger
from sqlalchemy import select

from app.coach.chat_utils.dispatcher import dispatch_coach_chat
from app.coach.chat_utils.schemas import CoachChatRequest, CoachChatResponse
from app.state.db import get_session
from app.state.models import CoachMessage, StravaAuth

router = APIRouter(prefix="/coach", tags=["coach"])


def _get_athlete_id() -> int | None:
    """Get athlete ID from the first StravaAuth entry.

    Returns:
        Athlete ID or None if no Strava auth exists
    """
    with get_session() as db:
        result = db.execute(select(StravaAuth)).first()
        if not result:
            return None
        return result[0].athlete_id


def _get_conversation_history(athlete_id: int, limit: int = 20) -> list[dict[str, str]]:
    """Get conversation history for an athlete.

    Args:
        athlete_id: Strava athlete ID
        limit: Maximum number of messages to retrieve (default: 20)

    Returns:
        List of messages with 'role' and 'content' keys, ordered by timestamp
    """
    with get_session() as db:
        messages = (
            db.query(CoachMessage)
            .filter(CoachMessage.athlete_id == athlete_id)
            .order_by(CoachMessage.timestamp.desc())
            .limit(limit)
            .all()
        )
        # Reverse to get chronological order (oldest first)
        return [
            {"role": msg.role, "content": msg.content}
            for msg in reversed(messages)
        ]


def _is_history_empty(athlete_id: int | None = None) -> bool:
    """Check if coach chat history is empty for an athlete.

    Args:
        athlete_id: Optional athlete ID. If None, checks the first athlete from StravaAuth.

    Returns:
        True if history is empty (cold start), False otherwise.
    """
    if athlete_id is None:
        athlete_id = _get_athlete_id()
        if athlete_id is None:
            return True

    with get_session() as db:
        message_count = db.query(CoachMessage).filter(CoachMessage.athlete_id == athlete_id).count()
        return message_count == 0


@router.post("/chat", response_model=CoachChatResponse)
async def coach_chat(req: CoachChatRequest) -> CoachChatResponse:
    logger.info(f"Coach chat request: {req.message}")

    # Get athlete ID
    athlete_id = _get_athlete_id()

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty(athlete_id)

    # Get conversation history (limit to last 20 messages for context)
    conversation_history = []
    if not history_empty and athlete_id is not None:
        conversation_history = _get_conversation_history(athlete_id, limit=20)
        logger.info(f"Retrieved {len(conversation_history)} messages from conversation history")

    intent, reply = dispatch_coach_chat(
        message=req.message,
        days=req.days,
        days_to_race=req.days_to_race,
        history_empty=history_empty,
        conversation_history=conversation_history,
    )

    return CoachChatResponse(
        intent=intent,
        reply=reply,
    )
