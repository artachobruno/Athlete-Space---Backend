from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

from app.coach.runtime.intents import CoachIntent
from app.coach.schemas.router import IntentRouterResponse
from app.config.settings import settings

# -------------------------------------------------
# Prompt
# -------------------------------------------------
INTENT_ROUTER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """
You are an intent classifier for Virtus Coach.

Your job is ONLY to classify the user's request.
You must NOT provide advice or explanations.

Allowed intents:
- next_session       → asking what workout to do next
- adjust_load        → asking to modify training based on feedback or fatigue
- explain_state      → asking to explain current fitness, fatigue, or readiness
- plan_race          → asking to plan training for a specific race
- plan_season        → asking to plan a full season
- plan_week          → asking for a weekly training plan
- add_workout        → asking to add a specific workout to the plan
- run_analysis       → asking for training analysis or insights
- share_report       → asking to generate or share a training report
- unsupported        → anything else

Return JSON ONLY.
            """.strip(),
    ),
    ("human", "{user_message}"),
])


# -------------------------------------------------
# Model
# -------------------------------------------------
_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
    api_key=SecretStr(settings.openai_api_key) if settings.openai_api_key else None,
)


# -------------------------------------------------
# Public API
# -------------------------------------------------
def route_intent(user_message: str) -> CoachIntent:
    """Classify user message into a supported coaching intent.

    This function MUST NOT:
    - Generate coaching advice
    - Generate plans
    - Generate explanations
    """
    logger.info("Routing coach intent", message=user_message)

    chain = INTENT_ROUTER_PROMPT | _llm.with_structured_output(IntentRouterResponse)

    raw_result = chain.invoke({"user_message": user_message})

    if isinstance(raw_result, IntentRouterResponse):
        result = raw_result
    else:
        result = IntentRouterResponse.model_validate(raw_result)

    logger.info("Intent resolved", intent=result.intent)

    return result.intent
