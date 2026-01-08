from fastapi import APIRouter
from loguru import logger
from sqlalchemy import select

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import USER_FACING_MODEL
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.coach.utils.context_management import save_context
from app.coach.utils.schemas import CoachChatRequest, CoachChatResponse
from app.db.models import CoachMessage, StravaAccount, StravaAuth
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

    # Convert athlete_id to user_id
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.debug("No user_id found for athlete_id, treating as cold start", athlete_id=athlete_id)
        return True

    with get_session() as db:
        message_count = db.query(CoachMessage).filter(CoachMessage.user_id == user_id).count()
        logger.debug(
            "Checking coach message history",
            athlete_id=athlete_id,
            user_id=user_id,
            message_count=message_count,
            is_empty=message_count == 0,
        )

        # Also check what user_ids actually exist in the table for debugging
        if message_count == 0:
            existing_user_ids = db.query(CoachMessage.user_id).distinct().all()
            existing_ids_list = [row[0] for row in existing_user_ids] if existing_user_ids else []
            logger.debug(
                "No messages found for user_id, checking existing user_ids in table",
                searched_athlete_id=athlete_id,
                searched_user_id=user_id,
                existing_user_ids=existing_ids_list,
                total_messages_in_table=db.query(CoachMessage).count(),
            )

        return message_count == 0


def _is_simple_acknowledgment(message: str) -> bool:
    """Check if message is a simple activity acknowledgment that doesn't need agent processing.

    Args:
        message: User's message

    Returns:
        True if message is a simple acknowledgment that should be handled via fast-path
    """
    normalized = message.strip().lower()
    simple_acks = {
        "i ran yesterday",
        "i ran today",
        "i worked out",
        "i trained today",
        "ran yesterday",
        "ran today",
        "worked out",
        "trained today",
    }
    return normalized in simple_acks


def get_or_create_athlete_id(db, user_id: str) -> int | None:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        db: Database session
        user_id: User ID to resolve athlete_id for

    Returns:
        Athlete ID as integer or None if not found
    """
    result = db.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
    if not result:
        return None
    return int(result[0].athlete_id)


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
            model_name=USER_FACING_MODEL,
            user_message=req.message,
            assistant_message=reply,
        )

        return CoachChatResponse(
            intent="cold_start",
            reply=reply,
        )

    # Fast-path: Handle simple activity acknowledgments without invoking agent
    # This prevents internal looping in pydantic_ai for trivial conversational inputs
    if _is_simple_acknowledgment(req.message):
        logger.info(
            "Fast-path: Handling simple acknowledgment without agent",
            message=req.message,
            athlete_id=athlete_id,
        )
        # Resolve athlete_id from user_id before fast-path return
        # This ensures athlete_id is always non-null when save_context is called
        with get_session() as db:
            resolved_athlete_id = get_or_create_athlete_id(db=db, user_id=user_id)
            if not resolved_athlete_id:
                raise RuntimeError("athlete_id could not be resolved in coach_chat fast-path")
            athlete_id = resolved_athlete_id

        reply = "Nice work 👍 Want feedback on recovery, pacing, or tomorrow's plan?"
        # Save conversation history for fast-path responses
        save_context(
            athlete_id=athlete_id,
            model_name=USER_FACING_MODEL,
            user_message=req.message,
            assistant_message=reply,
        )
        return CoachChatResponse(
            intent="activity_ack",
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
