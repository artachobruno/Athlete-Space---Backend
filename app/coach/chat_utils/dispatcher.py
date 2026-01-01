from loguru import logger

from app.coach.chat_utils.intent_router import CoachIntent, route_intent
from app.coach.state_builder import build_athlete_state
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_race import plan_race_build
from app.coach.tools.plan_season import plan_season
from app.state.api_helpers import get_training_data


def dispatch_coach_chat(
    message: str,
    days: int,
    days_to_race: int | None,
) -> tuple[str, str]:
    """Route user message -> coaching tool -> response text."""
    intent = route_intent(message)

    logger.info(f"Dispatching coach intent: {intent}")

    if intent == CoachIntent.UNSUPPORTED:
        return (
            intent.value,
            "I can help with training plans, fatigue adjustments, race builds, or explaining your current fitness.",
        )

    # -------------------------------------------------
    # Build athlete state ONCE
    # -------------------------------------------------
    try:
        training_data = get_training_data(days=days)
    except RuntimeError as e:
        logger.warning(f"No training data available: {e}")
        return (
            "insufficient_data",
            (
                "I don't have enough training data yet. "
                "Please make sure your Strava account is connected and synced. "
                "Once I have at least 14 days of training data, "
                "I'll be able to provide personalized coaching insights."
            ),
        )

    athlete_state = build_athlete_state(
        ctl=training_data.ctl,
        atl=training_data.atl,
        tsb=training_data.tsb,
        daily_load=training_data.daily_load,
        days_to_race=days_to_race,
    )

    # -------------------------------------------------
    # Tool routing
    # -------------------------------------------------
    if intent == CoachIntent.NEXT_SESSION:
        reply = recommend_next_session(athlete_state)

    elif intent == CoachIntent.ADJUST_LOAD:
        reply = adjust_training_load(athlete_state, message)

    elif intent == CoachIntent.EXPLAIN_STATE:
        reply = explain_training_state(athlete_state)

    elif intent == CoachIntent.PLAN_RACE:
        reply = plan_race_build(message)

    elif intent == CoachIntent.PLAN_SEASON:
        reply = plan_season()

    else:
        reply = "Unsupported request."

    return intent.value, reply
