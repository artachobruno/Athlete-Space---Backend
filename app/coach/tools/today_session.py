from loguru import logger

from app.coach.models import AthleteState


def generate_today_session(state: AthleteState) -> str:
    logger.info(f"Tool generate_today_session called (TSB={state.tsb:.1f})")
    tsb = state.tsb

    if tsb < -15:
        return "🛑 Rest day. Fatigue is high — prioritize recovery."

    if tsb < -8:
        return "Easy aerobic run, 30 to 45 min. Keep effort conversational."

    if tsb < 0:
        return "Moderate aerobic run, 45 to 60 min with relaxed strides."

    return "Quality session: intervals or tempo depending on current phase."
