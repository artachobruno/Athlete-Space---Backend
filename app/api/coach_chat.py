from fastapi import APIRouter
from loguru import logger
from sqlalchemy import select

from app.coach.chat_utils.schemas import CoachChatRequest, CoachChatResponse
from app.coach.orchestrator_agent import run_conversation
from app.coach.orchestrator_deps import CoachDeps
from app.coach.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.state.api_helpers import get_training_data
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
    """Handle coach chat request using orchestrator agent."""
    logger.info(f"Coach chat request: {req.message}")

    # Get athlete ID
    athlete_id = _get_athlete_id()
    if athlete_id is None:
        logger.warning("No athlete ID found, cannot process coach chat")
        return CoachChatResponse(
            intent="error",
            reply="Please connect your Strava account first.",
        )

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty(athlete_id)

    # Handle cold start
    if history_empty:
        logger.info("Cold start detected - providing welcome message")
        try:
            training_data = get_training_data(days=req.days)
            athlete_state = build_athlete_state(
                ctl=training_data.ctl,
                atl=training_data.atl,
                tsb=training_data.tsb,
                daily_load=training_data.daily_load,
                days_to_race=req.days_to_race,
            )
            reply = welcome_new_user(athlete_state)
        except RuntimeError:
            logger.warning("Cold start with no training data available")
            reply = welcome_new_user(None)

        return CoachChatResponse(
            intent="cold_start",
            reply=reply,
        )

    # Build athlete state
    try:
        training_data = get_training_data(days=req.days)
        athlete_state = build_athlete_state(
            ctl=training_data.ctl,
            atl=training_data.atl,
            tsb=training_data.tsb,
            daily_load=training_data.daily_load,
            days_to_race=req.days_to_race,
        )
    except RuntimeError:
        logger.warning("No training data available for orchestrator")
        athlete_state = None

    # Create dependencies
    deps = CoachDeps(
        athlete_id=athlete_id,
        athlete_state=athlete_state,
        days=req.days,
        days_to_race=req.days_to_race,
    )

    # Run orchestrator
    result = await run_conversation(
        user_input=req.message,
        deps=deps,
    )

    return CoachChatResponse(
        intent=result.intent,
        reply=result.message,
    )
