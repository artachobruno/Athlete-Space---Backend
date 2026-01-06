from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.schemas.athlete_state import AthleteState
from app.config.settings import settings


def run_analysis(state: AthleteState) -> str:
    """Run comprehensive training analysis on current state using LLM.

    Args:
        state: Current athlete state with all metrics.

    Returns:
        Detailed analysis of training state, trends, and insights.
    """
    logger.info(f"Tool run_analysis called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, TSB={state.tsb:.1f}, flags={state.flags})")

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot run analysis with LLM")
        return "I'd love to run a comprehensive analysis! Please ensure OpenAI API key is configured for detailed training analysis."

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to provide a comprehensive training analysis based on the athlete's current training state.

Provide a detailed analysis that includes:
1. Load Metrics Summary (CTL, ATL, TSB)
2. Volume Analysis (7-day and 14-day volumes, trends)
3. Readiness & Fatigue Assessment
4. Risk Flags (if any)
5. Training Consistency (volatility)
6. Load Trend Analysis
7. Key Recommendations

Format the analysis clearly with sections. Use appropriate emojis or markers for visual clarity.
Be comprehensive but concise. Use plain language and avoid excessive jargon."""

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
            f"- Confidence: {state.confidence:.2f}\n"
        )

        if state.flags:
            athlete_state_str += f"- Flags: {', '.join(state.flags)}\n"

        if state.days_to_race:
            athlete_state_str += f"- Days to race: {state.days_to_race}\n"

        user_prompt = f"{athlete_state_str}\n\nProvide a comprehensive training analysis:"

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
        logger.error(f"Error running analysis with LLM: {e}", exc_info=True)
        return "I encountered an error generating the training analysis. Please try again."
