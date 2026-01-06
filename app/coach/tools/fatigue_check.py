from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.core.models import AthleteState
from app.config.settings import settings


def fatigue_check(state: AthleteState) -> str:
    """Check athlete fatigue level using LLM.

    Args:
        state: Current athlete training state

    Returns:
        Fatigue assessment string
    """
    logger.info(f"Tool fatigue_check called (TSB={state.tsb:.1f}, confidence={state.confidence:.2f})")

    if state.confidence < 0.1:
        return (
            "I'd like to help assess your fatigue! To give you accurate guidance, could you tell me:\n\n"
            "• How are you feeling overall? (energetic, tired, or somewhere in between?)\n"
            "• How has your training been lately? (consistent, increased volume, or taking it easy?)\n"
            "• Any signs of fatigue? (soreness, trouble sleeping, decreased motivation?)\n\n"
            "Based on your answers, I can provide personalized recovery recommendations. "
            "Syncing your Strava activities will also help me track your training load over time!"
        )

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot assess fatigue with LLM")
        return "I'd love to assess your fatigue! Please ensure OpenAI API key is configured for personalized assessments."

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to assess the athlete's fatigue level based on their training state.

Consider:
- Training Stress Balance (TSB): Negative values indicate fatigue, positive values indicate freshness
- Chronic Training Load (CTL): Long-term fitness level
- Acute Training Load (ATL): Recent training load
- Load trends and volatility
- Days since rest
- Current training volume

Provide a clear fatigue assessment. Be concise and practical. Avoid explaining metrics."""

        athlete_state_str = (
            f"Training State:\n"
            f"- CTL (fitness): {state.ctl:.1f}\n"
            f"- ATL (fatigue): {state.atl:.1f}\n"
            f"- TSB (balance): {state.tsb:.1f}\n"
            f"- Load trend: {state.load_trend}\n"
            f"- Volatility: {state.volatility}\n"
            f"- Days since rest: {state.days_since_rest}\n"
            f"- 7-day volume: {state.seven_day_volume_hours:.1f} hours\n"
        )

        if state.flags:
            athlete_state_str += f"- Flags: {', '.join(state.flags)}\n"

        user_prompt = f"{athlete_state_str}\nAssess fatigue level:"

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
        logger.error(f"Error assessing fatigue with LLM: {e}", exc_info=True)
        return "I encountered an error assessing your fatigue. Please try again."
