from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.schemas.athlete_state import AthleteState
from app.config.settings import settings


def explain_training_state(state: AthleteState) -> str:
    """Explain current fitness, fatigue, and readiness in plain language using LLM.

    Args:
        state: Current athlete training state

    Returns:
        Explanation string in plain language
    """
    logger.info(
        f"Tool explain_training_state called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, "
        f"TSB={state.tsb:.1f}, confidence={state.confidence:.2f})"
    )

    if state.confidence < 0.1:
        return (
            "I'd love to explain your training state! To give you accurate insights, could you tell me:\n\n"
            "• How consistent has your training been? (daily, a few times per week, or irregular?)\n"
            "• What's your typical training volume? (hours per week?)\n"
            "• How are you feeling? (energetic, tired, or somewhere in between?)\n"
            "• What's your training goal right now? (building base, race prep, or maintaining?)\n\n"
            "Based on your answers, I can explain your current state and provide guidance. "
            "Syncing your Strava activities will help me provide even more detailed analysis!"
        )

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot explain state with LLM")
        return "I'd love to explain your training state! Please ensure OpenAI API key is configured for personalized explanations."

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to explain the athlete's training state in plain, understandable language.

Explain:
- Current fitness level (CTL)
- Current fatigue level (ATL)
- Training Stress Balance (TSB) - what it means for them
- Overall state and what they should know

Use plain language. Avoid jargon. Be helpful and informative."""

        athlete_state_str = (
            f"Training State:\n"
            f"- CTL (fitness): {state.ctl:.1f}\n"
            f"- ATL (fatigue): {state.atl:.1f}\n"
            f"- TSB (balance): {state.tsb:.1f}\n"
            f"- Load trend: {state.load_trend}\n"
            f"- Volatility: {state.volatility}\n"
            f"- Days since rest: {state.days_since_rest}\n"
            f"- 7-day volume: {state.seven_day_volume_hours:.1f} hours\n"
            f"- 14-day volume: {state.fourteen_day_volume_hours:.1f} hours\n"
        )

        if state.flags:
            athlete_state_str += f"- Flags: {', '.join(state.flags)}\n"

        if state.days_to_race:
            athlete_state_str += f"- Days to race: {state.days_to_race}\n"

        user_prompt = f"{athlete_state_str}\nExplain this training state in plain language:"

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", user_prompt),
        ])

        chain = prompt | llm
        result = chain.invoke({})

        if hasattr(result, "content"):
            content = result.content
            if isinstance(content, str):
                return content
            return str(content)
        return str(result)

    except Exception as e:
        logger.error(f"Error explaining training state with LLM: {e}", exc_info=True)
        return "I encountered an error explaining your training state. Please try again."
