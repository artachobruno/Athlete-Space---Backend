from loguru import logger
from pydantic import SecretStr

from app.coach.chat_utils.intent_router import CoachIntent, route_intent
from app.coach.models import AthleteState
from app.coach.state_builder import build_athlete_state
from app.coach.tools.add_workout import add_workout
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.cold_start import welcome_new_user
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_race import plan_race_build
from app.coach.tools.plan_season import plan_season
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
from app.core.settings import settings
from app.state.api_helpers import get_training_data

try:
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI

    LANGCHAIN_LLM_AVAILABLE = True
except ImportError:
    LANGCHAIN_LLM_AVAILABLE = False
    ChatOpenAI = None
    HumanMessage = None

try:
    from app.coach.orchestrator import run_orchestrator

    ORCHESTRATOR_AVAILABLE = True
except ImportError:
    ORCHESTRATOR_AVAILABLE = False
    run_orchestrator = None


def _answer_general_question_with_llm(question: str) -> str:
    """Answer general questions using LLM when training data is not available.

    Args:
        question: User's question

    Returns:
        LLM-generated response
    """
    if not LANGCHAIN_LLM_AVAILABLE or ChatOpenAI is None or HumanMessage is None:
        logger.warning("LLM not available for general questions")
        return (
            "I'm here to help with your training, but I need your Strava account connected "
            "and some training data synced to provide personalized guidance. "
            "For now, I can answer general training questions once my LLM features are configured."
        )

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot answer general questions")
        return (
            "I'd love to help, but I need your training data synced first. "
            "Please connect your Strava account and sync some activities, then I can provide personalized coaching advice."
        )

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        prompt_text = f"""You are Virtus Coach, an elite endurance training intelligence system.
You provide expert, personalized coaching advice based on training science.

Note: The athlete's training data is not yet available, but you can still provide helpful general training advice.

User question: {question}

Provide a helpful, knowledgeable answer about training, technique, or general endurance coaching.
Keep responses concise (2-3 paragraphs max) and actionable. Focus on practical training advice.
If the question requires personalized data (like current fitness or fatigue), explain that you'll be able to provide more specific guidance once their training data is synced."""

        logger.info("Invoking LLM for general question (no training data)")
        response = llm.invoke([HumanMessage(content=prompt_text)])
        if not hasattr(response, "content"):
            logger.warning("LLM response missing content attribute")
            return str(response)
        content = response.content
        if isinstance(content, str):
            logger.info("General question answered successfully via LLM")
            return content
        if isinstance(content, list):
            logger.info("General question answered successfully via LLM (list content)")
            return " ".join(str(item) for item in content if isinstance(item, str))
        logger.info("General question answered successfully via LLM (converted content)")
        return str(content)
    except Exception as e:
        logger.error(f"Error answering general question with LLM: {e}", exc_info=True)
        return (
            "I encountered an error processing your question. "
            "Please make sure your Strava account is connected and synced, "
            "or try rephrasing your question."
        )


def _handle_cold_start(days: int, days_to_race: int | None) -> tuple[str, str]:
    """Handle cold start scenario - provide welcome message."""
    logger.info(f"Cold start detected - providing welcome message (days={days}, days_to_race={days_to_race})")
    try:
        logger.info("Fetching training data for cold start")
        training_data = get_training_data(days=days)
        logger.info("Building athlete state for cold start")
        athlete_state = build_athlete_state(
            ctl=training_data.ctl,
            atl=training_data.atl,
            tsb=training_data.tsb,
            daily_load=training_data.daily_load,
            days_to_race=days_to_race,
        )
        logger.info("Calling welcome_new_user tool")
        reply = welcome_new_user(athlete_state)
    except RuntimeError:
        # Even if we don't have training data, provide a welcome message
        logger.warning("Cold start with no training data available")
        reply = welcome_new_user(None)

    return ("cold_start", reply)


def _get_athlete_state(days: int, days_to_race: int | None) -> tuple[str, str] | tuple[None, AthleteState]:
    """Get athlete state or return error response.
    
    Returns:
        Tuple of (error_type, error_message) if error, or (None, AthleteState) if successful
    """
    logger.info(f"Getting athlete state (days={days}, days_to_race={days_to_race})")
    try:
        logger.info("Fetching training data")
        training_data = get_training_data(days=days)
    except RuntimeError as e:
        logger.warning(f"No training data available: {e}")
        # Return a special error type that allows LLM fallback
        return ("no_training_data", "")

    logger.info("Building athlete state from training data")
    athlete_state = build_athlete_state(
        ctl=training_data.ctl,
        atl=training_data.atl,
        tsb=training_data.tsb,
        daily_load=training_data.daily_load,
        days_to_race=days_to_race,
    )
    logger.info(f"Athlete state built successfully (CTL={athlete_state.ctl:.1f}, ATL={athlete_state.atl:.1f}, TSB={athlete_state.tsb:.1f})")
    return (None, athlete_state)


def _route_to_tool(intent: CoachIntent, athlete_state: AthleteState, message: str) -> str:
    """Route intent to appropriate coaching tool."""
    logger.info(f"Routing to tool: {intent.value}")
    tool_map: dict[CoachIntent, str] = {
        CoachIntent.NEXT_SESSION: recommend_next_session(athlete_state),
        CoachIntent.ADJUST_LOAD: adjust_training_load(athlete_state, message),
        CoachIntent.EXPLAIN_STATE: explain_training_state(athlete_state),
        CoachIntent.PLAN_RACE: plan_race_build(message),
        CoachIntent.PLAN_SEASON: plan_season(),
        CoachIntent.PLAN_WEEK: plan_week(athlete_state),
        CoachIntent.ADD_WORKOUT: add_workout(athlete_state, message),
        CoachIntent.RUN_ANALYSIS: run_analysis(athlete_state),
        CoachIntent.SHARE_REPORT: share_report(athlete_state),
    }
    result = tool_map.get(intent, "Unsupported request.")
    logger.info(f"Tool {intent.value} completed successfully")
    return result


def dispatch_coach_chat(
    message: str,
    days: int,
    days_to_race: int | None,
    history_empty: bool = False,
    use_orchestrator: bool = True,
) -> tuple[str, str]:
    """Route user message -> coaching tool -> response text.

    Args:
        message: User's message to the coach
        days: Number of days of training data to consider
        days_to_race: Optional days until race
        history_empty: If True, this is a cold start (first message).
                      Will return welcome message instead of routing intent.
        use_orchestrator: If True, use the LangChain orchestrator (default: True).
                         Falls back to intent routing if orchestrator unavailable.
    """
    logger.info(
        f"Dispatching coach chat (message_length={len(message)}, days={days}, "
        f"days_to_race={days_to_race}, history_empty={history_empty}, "
        f"use_orchestrator={use_orchestrator})"
    )

    # Handle cold start - provide welcome message regardless of intent
    if history_empty:
        logger.info("Handling cold start scenario")
        return _handle_cold_start(days, days_to_race)

    logger.info("Routing intent from user message")
    intent = route_intent(message)
    logger.info(f"Dispatching coach intent: {intent}")

    # Build athlete state
    logger.info("Building athlete state for tool routing")
    state_result = _get_athlete_state(days, days_to_race)
    if state_result[0] is not None:  # Error response
        error_type = state_result[0]
        logger.warning(f"Failed to get athlete state: {error_type}")
        # If no training data, use LLM to answer general questions
        if error_type == "no_training_data":
            logger.info("No training data available, using LLM for general question")
            reply = _answer_general_question_with_llm(message)
            return ("general_question", reply)
        # For other errors, return the error message
        return state_result  # type: ignore[return-value]

    # At this point, state_result[0] is None, so state_result[1] is AthleteState
    athlete_state = state_result[1]

    # Use orchestrator if requested and available
    if use_orchestrator and ORCHESTRATOR_AVAILABLE and run_orchestrator is not None:
        logger.info("Using LLM orchestrator for coach chat (LangChain agent with tools)")
        try:
            logger.info(f"Calling LLM orchestrator with message: {message[:100]}")
            reply = run_orchestrator(message, athlete_state)
            logger.info(f"LLM orchestrator completed successfully, reply length: {len(reply)}")
            return ("orchestrator", reply)
        except Exception as e:
            logger.error(f"LLM orchestrator failed, falling back to intent routing: {e}", exc_info=True)
            # Fall through to intent-based routing

    # Route to appropriate tool (intent routing uses LLM, tools may be rule-based)
    logger.info(f"Using intent routing with LLM (intent={intent.value})")
    reply = _route_to_tool(intent, athlete_state, message)
    logger.info(f"Dispatch completed successfully with intent: {intent.value}, reply length: {len(reply)}")
    return intent.value, reply
