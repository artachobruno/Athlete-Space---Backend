from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.coach.intents import CoachIntent


class IntentResult(BaseModel):
    intent: CoachIntent


_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.0,
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
    raw_result = _chain.invoke({"message": message})

    if isinstance(raw_result, IntentResult):
        result = raw_result
    else:
        result = IntentResult.model_validate(raw_result)

    return result.intent
