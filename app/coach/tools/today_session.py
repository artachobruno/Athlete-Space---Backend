from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.config.models import USER_FACING_MODEL
from app.coach.schemas.athlete_state import AthleteState
from app.config.settings import settings


def generate_today_session(state: AthleteState) -> str:
    """Generate today's session recommendation using LLM.

    Args:
        state: Current athlete training state

    Returns:
        Session recommendation string
    """
    logger.info(f"Tool generate_today_session called (TSB={state.tsb:.1f})")

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot generate session with LLM")
        return "I'd love to recommend today's session! Please ensure OpenAI API key is configured for personalized recommendations."

    try:
        llm = ChatOpenAI(
            model=USER_FACING_MODEL,
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to recommend today's training session based on the athlete's current training state.

Consider:
- Training Stress Balance (TSB): Negative values indicate fatigue, positive values indicate freshness
- Chronic Training Load (CTL): Long-term fitness level
- Acute Training Load (ATL): Recent training load
- Load trends and volatility
- Days since rest
- Current training volume

Provide a clear, concise session recommendation. Be practical and coach-like. Avoid explaining metrics."""

        athlete_state_str = (
            f"Training State:\n"
            f"- CTL (fitness): {state.ctl:.1f}\n"
            f"- ATL (fatigue): {state.atl:.1f}\n"
            f"- TSB (balance): {state.tsb:.1f}\n"
            f"- Load trend: {state.load_trend}\n"
            f"- Days since rest: {state.days_since_rest}\n"
            f"- 7-day volume: {state.seven_day_volume_hours:.1f} hours\n"
        )

        if state.flags:
            athlete_state_str += f"- Flags: {', '.join(state.flags)}\n"

        user_prompt = f"{athlete_state_str}\nRecommend today's session:"

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
        logger.error(f"Error generating today's session with LLM: {e}", exc_info=True)
        return "I encountered an error generating your session recommendation. Please try again."
