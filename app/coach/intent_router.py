from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import BaseModel, SecretStr

from app.coach.intents import CoachIntent
from app.core.settings import settings


class IntentResult(BaseModel):
    intent: CoachIntent


# Initialize LLM only if API key is available
if not settings.openai_api_key:
    logger.warning("OPENAI_API_KEY is not set. Intent routing LLM features will not work.")
    _llm = None
    _chain = None
else:
    _llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.0,
        api_key=SecretStr(settings.openai_api_key),
    )

    _prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """
You are an intent classifier for an endurance training coach.

Choose ONE intent only.

TODAY_SESSION:
- asking what to do today
- today's workout
- session for today

FATIGUE_CHECK:
- am I tired
- should I rest
- fatigue / soreness / burnout

LOAD_EXPLANATION:
- explain CTL, ATL, TSB
- training load explanation

FREE_CHAT:
- anything else
""",
        ),
        ("human", "{message}"),
    ])

    _chain = _prompt | _llm.with_structured_output(IntentResult)


def route_intent(message: str) -> CoachIntent:
    """Route user message to appropriate coach intent using LLM.

    Args:
        message: User's message to classify

    Returns:
        CoachIntent enum value

    Raises:
        ValueError: If OPENAI_API_KEY is not configured
        RuntimeError: If LLM call or parsing fails
    """
    if _chain is None or not settings.openai_api_key:
        logger.warning("Intent routing LLM not available, defaulting to FREE_CHAT")
        return CoachIntent.FREE_CHAT

    try:
        # Log LLM model being called
        model_name = "gpt-4o-mini" if _llm is not None else "unknown"
        logger.info(
            "Calling intent router LLM",
            model=model_name,
            provider="openai",
        )

        # Log prompt at debug level
        system_prompt = """
You are an intent classifier for an endurance training coach.

Choose ONE intent only.

TODAY_SESSION:
- asking what to do today
- today's workout
- session for today

FATIGUE_CHECK:
- am I tired
- should I rest
- fatigue / soreness / burnout

LOAD_EXPLANATION:
- explain CTL, ATL, TSB
- training load explanation

FREE_CHAT:
- anything else
"""
        full_prompt_text = f"System Prompt:\n{system_prompt}\n\nUser Message:\n{message}"
        logger.debug(
            "Intent router prompt",
            prompt_length=len(full_prompt_text),
            system_prompt_length=len(system_prompt),
            user_message_length=len(message),
            full_prompt=full_prompt_text,
        )

        logger.info("Routing intent with LLM")
        raw_result = _chain.invoke({"message": message})

        if isinstance(raw_result, IntentResult):
            result = raw_result
        else:
            result = IntentResult.model_validate(raw_result)

        # Log response at debug level
        logger.debug(
            "Intent router response",
            intent=result.intent.value,
            full_response=result.model_dump_json(indent=2),
        )

        # Log intent decision at info level
        logger.info(
            "Intent decision",
            intent=result.intent.value,
            user_message_preview=message[:100],
        )
    except Exception as e:
        logger.error(f"Error routing intent: {type(e).__name__}: {e}", exc_info=True)
        logger.warning("Falling back to FREE_CHAT intent due to error")
        return CoachIntent.FREE_CHAT
    else:
        return result.intent
