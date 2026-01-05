from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.models import AthleteState
from app.core.settings import settings


def adjust_training_load(state: AthleteState, message: str) -> str:
    """Adjust training load based on athlete feedback using LLM.

    Args:
        state: Current athlete training state
        message: User's feedback or request about training load

    Returns:
        Training load adjustment recommendation
    """
    logger.info(f"Tool adjust_training_load called (message_length={len(message)}, TSB={state.tsb:.1f}, confidence={state.confidence:.2f})")

    if state.confidence < 0.1:
        return (
            "I'd like to help adjust your training load! To give you the best recommendations, could you tell me:\n\n"
            "• How are you feeling? (tired, strong, or somewhere in between?)\n"
            "• What's your current training volume? (hours per week or sessions per week?)\n"
            "• What's your goal? (building fitness, maintaining, or recovering?)\n"
            "• Any specific concerns? (overtraining, undertraining, or just fine-tuning?)\n\n"
            "Based on your answers, I can suggest specific adjustments. "
            "Syncing your Strava activities will help me provide even more precise recommendations!"
        )

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot adjust load with LLM")
        return "I'd love to help adjust your training load! Please ensure OpenAI API key is configured for personalized recommendations."

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to recommend training load adjustments based on the athlete's feedback and current training state.

Consider:
- Training Stress Balance (TSB): Negative values indicate fatigue, positive values indicate freshness
- Chronic Training Load (CTL): Long-term fitness level
- Acute Training Load (ATL): Recent training load
- Load trends and volatility
- The athlete's feedback about how they're feeling
- Current training volume

Provide specific, actionable recommendations for adjusting training load. Be practical and coach-like."""

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

        user_prompt = f"{athlete_state_str}\nAthlete feedback: {message}\n\nRecommend training load adjustments:"

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
        logger.error(f"Error adjusting training load with LLM: {e}", exc_info=True)
        return "I encountered an error generating load adjustments. Please try again."
