from loguru import logger

from app.coach.models import AthleteState


def fatigue_check(state: AthleteState) -> str:
    logger.info(f"Tool fatigue_check called (TSB={state.tsb:.1f}, confidence={state.confidence:.2f})")

    # Check confidence - ask clarifying questions with insufficient data
    if state.confidence < 0.1:
        return (
            "I'd like to help assess your fatigue! To give you accurate guidance, could you tell me:\n\n"
            "• How are you feeling overall? (energetic, tired, or somewhere in between?)\n"
            "• How has your training been lately? (consistent, increased volume, or taking it easy?)\n"
            "• Any signs of fatigue? (soreness, trouble sleeping, decreased motivation?)\n\n"
            "Based on your answers, I can provide personalized recovery recommendations. "
            "Syncing your Strava activities will also help me track your training load over time!"
        )

    if state.tsb < -15:
        return "High fatigue detected. Risk of overreaching."

    if state.tsb < -8:
        return "Moderate fatigue. Manage intensity carefully."

    if state.tsb < 0:
        return "Normal training fatigue."

    return "You are fresh and well recovered."
