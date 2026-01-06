from fastapi import APIRouter
from loguru import logger
from sqlalchemy import select

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.coach.utils.context_management import save_context
from app.coach.utils.schemas import CoachChatRequest, CoachChatResponse
from app.db.models import CoachMessage, StravaAuth
from app.db.session import get_session
from app.state.api_helpers import get_training_data, get_user_id_from_athlete_id

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
            logger.debug("No athlete_id found, treating as cold start")
            return True

    with get_session() as db:
        message_count = db.query(CoachMessage).filter(CoachMessage.athlete_id == athlete_id).count()
        logger.debug(
            "Checking coach message history",
            athlete_id=athlete_id,
            athlete_id_type=type(athlete_id).__name__,
            message_count=message_count,
            is_empty=message_count == 0,
        )

        # Also check what athlete_ids actually exist in the table for debugging
        if message_count == 0:
            existing_athlete_ids = db.query(CoachMessage.athlete_id).distinct().all()
            existing_ids_list = [row[0] for row in existing_athlete_ids] if existing_athlete_ids else []
            logger.debug(
                "No messages found for athlete_id, checking existing athlete_ids in table",
                searched_athlete_id=athlete_id,
                searched_athlete_id_type=type(athlete_id).__name__,
                existing_athlete_ids=existing_ids_list,
                existing_athlete_id_types=[type(row[0]).__name__ for row in existing_athlete_ids] if existing_athlete_ids else [],
                total_messages_in_table=db.query(CoachMessage).count(),
            )

        return message_count == 0


@router.post("/chat", response_model=CoachChatResponse)
async def coach_chat(req: CoachChatRequest) -> CoachChatResponse:
    """Handle coach chat request using orchestrator agent."""
    logger.info(f"Coach chat request: {req.message}")

    # Get athlete ID
    athlete_id = _get_athlete_id()
    logger.debug(
        "Retrieved athlete_id for coach chat",
        athlete_id=athlete_id,
        athlete_id_type=type(athlete_id).__name__ if athlete_id is not None else None,
    )
    if athlete_id is None:
        logger.warning("No athlete ID found, cannot process coach chat")
        return CoachChatResponse(
            intent="error",
            reply="Please connect your Strava account first.",
        )

    # Check if this is a cold start (empty history)
    history_empty = _is_history_empty(athlete_id)
    logger.debug(
        "Cold start check result",
        athlete_id=athlete_id,
        history_empty=history_empty,
    )

    # Get user_id from athlete_id
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.warning(f"Cannot find user_id for athlete_id={athlete_id}")
        return CoachChatResponse(
            intent="error",
            reply="Unable to find user account. Please reconnect your Strava account.",
        )

    # Handle cold start
    if history_empty:
        logger.info("Cold start detected - providing welcome message")
        try:
            training_data = get_training_data(user_id=user_id, days=req.days)
            athlete_state = build_athlete_state(
                ctl=training_data.ctl,
                atl=training_data.atl,
                tsb=training_data.tsb,
                daily_load=training_data.daily_load,
                days_to_race=req.days_to_race,
            )
            logger.debug(
                "Cold start with training data",
                athlete_id=athlete_id,
                ctl=athlete_state.ctl,
                atl=athlete_state.atl,
                tsb=athlete_state.tsb,
                confidence=athlete_state.confidence,
                load_trend=athlete_state.load_trend,
                flags=athlete_state.flags,
            )
            reply = welcome_new_user(athlete_state)
        except RuntimeError as e:
            logger.warning("Cold start with no training data available", error=str(e))
            reply = welcome_new_user(None)

        # Save conversation history for cold start
        save_context(
            athlete_id=athlete_id,
            model_name="gpt-4o-mini",
            user_message=req.message,
            assistant_message=reply,
        )

        return CoachChatResponse(
            intent="cold_start",
            reply=reply,
        )

    # Build athlete state
    try:
        training_data = get_training_data(user_id=user_id, days=req.days)
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
        user_id=user_id,
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
