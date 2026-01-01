from loguru import logger

from app.coach.models import AthleteState


def fatigue_check(state: AthleteState) -> str:
    logger.info(f"Tool fatigue_check called (TSB={state.tsb:.1f})")
    if state.tsb < -15:
        return "High fatigue detected. Risk of overreaching."

    if state.tsb < -8:
        return "Moderate fatigue. Manage intensity carefully."

    if state.tsb < 0:
        return "Normal training fatigue."

    return "You are fresh and well recovered."
