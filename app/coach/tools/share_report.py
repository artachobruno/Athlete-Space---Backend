from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.models import AthleteState
from app.core.settings import settings


def share_report(state: AthleteState) -> str:
    """Generate a shareable training report using LLM.

    Args:
        state: Current athlete state with all metrics.

    Returns:
        A formatted, shareable training report.
    """
    logger.info(f"Tool share_report called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, TSB={state.tsb:.1f})")

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot generate report with LLM")
        return "I'd love to generate a shareable report! Please ensure OpenAI API key is configured for report generation."

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=SecretStr(settings.openai_api_key),
        )

        report_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

        system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to generate a professional, shareable training report based on the athlete's current training state.

Format the report as a professional document with:
1. Header with title and date
2. Executive Summary (overall status, load trend)
3. Key Metrics (CTL, ATL, TSB, volumes)
4. Training Status (assessment in plain language)
5. Risk Assessment (if any flags exist)
6. Recommendations (actionable next steps)
7. Load Trend Analysis
8. Footer with attribution

Use clear formatting with section headers. Make it professional and shareable.
Use plain language and avoid excessive technical jargon."""

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

        user_prompt = f"Date: {report_date}\n\n{athlete_state_str}\n\nGenerate a professional, shareable training report:"

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
        logger.error(f"Error generating shareable report with LLM: {e}", exc_info=True)
        return "I encountered an error generating the training report. Please try again."
