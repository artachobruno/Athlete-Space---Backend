from app.coach.intent_router import route_intent
from app.coach.intents import CoachIntent
from app.coach.models import AthleteState
from app.coach.tools.fatigue_check import fatigue_check
from app.coach.tools.load_explanation import explain_load
from app.coach.tools.today_session import generate_today_session


def coach_chat(message: str, state: AthleteState) -> dict:
    intent = route_intent(message)

    if intent == CoachIntent.TODAY_SESSION:
        reply = generate_today_session(state)

    elif intent == CoachIntent.FATIGUE_CHECK:
        reply = fatigue_check(state)

    elif intent == CoachIntent.LOAD_EXPLANATION:
        reply = explain_load(state)

    else:
        reply = "I'm here to help with training, fatigue, and planning. Ask me about today's session or your recovery."

    return {
        "intent": intent.value,
        "reply": reply,
    }
