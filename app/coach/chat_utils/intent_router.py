from __future__ import annotations

from enum import StrEnum

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import BaseModel, Field, SecretStr

from app.core.settings import settings


# -------------------------------------------------
# Allowed intents (STRICT)
# -------------------------------------------------
class CoachIntent(StrEnum):
    NEXT_SESSION = "next_session"
    ADJUST_LOAD = "adjust_load"
    EXPLAIN_STATE = "explain_state"
    PLAN_RACE = "plan_race"
    PLAN_SEASON = "plan_season"
    UNSUPPORTED = "unsupported"


# -------------------------------------------------
# LLM output schema (enforced)
# -------------------------------------------------
class IntentRouterResponse(BaseModel):
    intent: CoachIntent = Field(description="One of the allowed coaching intents")


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
