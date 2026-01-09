import os
from datetime import date, datetime, timezone

from fastapi import APIRouter
from loguru import logger
from sqlalchemy import select

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import AthleteProfileData, CoachDeps, RaceProfileData, TrainingPreferencesData
from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.coach.utils.context_management import save_context
from app.coach.utils.schemas import CoachChatRequest, CoachChatResponse
from app.db.models import AthleteProfile, CoachMessage, StravaAuth, UserSettings
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

    # Load athlete profile, training preferences, and race profile
    athlete_profile = None
    training_preferences = None
    race_profile = None
    with get_session() as db:
        profile = db.query(AthleteProfile).filter_by(user_id=user_id).first()
        if profile:
            # Calculate age from date_of_birth
            age = None
            if profile.date_of_birth:
                today = datetime.now(timezone.utc).date()
                dob = profile.date_of_birth.date()
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

            # Round weight_lbs and height_in to 1 decimal place
            weight_lbs_rounded = None
            if profile.weight_lbs is not None:
                weight_lbs_rounded = round(float(profile.weight_lbs), 1)
            height_in_rounded = None
            if profile.height_in is not None:
                height_in_rounded = round(float(profile.height_in), 1)

            athlete_profile = AthleteProfileData(
                gender=profile.gender,
                age=age,
                weight_lbs=weight_lbs_rounded,
                height_in=height_in_rounded,
                unit_system=profile.unit_system or "imperial",
            )

            # Load race profile from extracted_race_attributes
            if profile.extracted_race_attributes and isinstance(profile.extracted_race_attributes, dict):
                race_attrs = profile.extracted_race_attributes
                race_profile = RaceProfileData(
                    event_name=race_attrs.get("event_name"),
                    event_type=race_attrs.get("event_type"),
                    event_date=race_attrs.get("event_date"),
                    target_time=race_attrs.get("target_time"),
                    distance=race_attrs.get("distance"),
                    location=race_attrs.get("location"),
                    raw_text=race_attrs.get("raw_text"),
                )

        # Load training preferences from UserSettings
        settings = db.query(UserSettings).filter_by(user_id=user_id).first()
        if settings:
            training_preferences = TrainingPreferencesData(
                training_consistency=settings.consistency,
                years_structured=settings.years_of_training,
                primary_sports=settings.primary_sports or [],
                available_days=settings.available_days or [],
                weekly_training_hours=settings.weekly_hours,
                primary_training_goal=settings.goal,
                training_focus=settings.training_focus,
                injury_flag=settings.injury_history or False,
            )

    # Create dependencies
    deps = CoachDeps(
        athlete_id=athlete_id,
        user_id=user_id,
        athlete_state=athlete_state,
        athlete_profile=athlete_profile,
        training_preferences=training_preferences,
        race_profile=race_profile,
        days=req.days,
        days_to_race=req.days_to_race,
    )

    # Get decision from orchestrator
    decision = await run_conversation(
        user_input=req.message,
        deps=deps,
    )

    # Execute action if needed
    reply = await CoachActionExecutor.execute(decision, deps)

    return CoachChatResponse(
        intent=decision.intent,
        reply=reply,
    )
