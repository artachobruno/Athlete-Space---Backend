from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.schemas.athlete_state import AthleteState
from app.config.settings import settings


def plan_week(state: AthleteState) -> str:
    """Generate a weekly training plan based on current state using LLM.

    Args:
        state: Current athlete state with load metrics and trends.

    Returns:
        A weekly training plan tailored to the athlete's current state.
    """
    logger.info(f"Tool plan_week called (TSB={state.tsb:.1f}, load_trend={state.load_trend}, flags={state.flags})")

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot generate weekly plan with LLM")
        return "I'd love to create a weekly plan! Please ensure OpenAI API key is configured for personalized training plans."

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to generate a weekly training plan based on the athlete's current training state.

Create a 7-day plan (Monday through Sunday) that includes:
- Specific sessions for each day
- Session types (easy run, tempo, intervals, long run, rest, etc.)
- Duration and intensity guidance
- Total weekly volume estimate
- Focus/goals for the week

Consider:
- Training Stress Balance (TSB): Negative values indicate fatigue (may need recovery),
  positive values indicate freshness (good for quality work)
- Load trends: rising, stable, or falling
- Current training volume
- Days since rest
- Any risk flags

Format the plan clearly with days of the week. Be specific and actionable. Provide a realistic, appropriate plan for their current state."""

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

        user_prompt = f"{athlete_state_str}\n\nGenerate a weekly training plan:"

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
        logger.error(f"Error generating weekly plan with LLM: {e}", exc_info=True)
        return "I encountered an error generating the weekly plan. Please try again."
