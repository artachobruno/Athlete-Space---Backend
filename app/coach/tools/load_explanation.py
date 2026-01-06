from loguru import logger

from app.coach.core.models import AthleteState


def explain_load(state: AthleteState) -> str:
    logger.info(f"Tool explain_load called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, TSB={state.tsb:.1f})")
    return (
        f"CTL (fitness): {state.ctl:.1f}\n"
        f"ATL (fatigue): {state.atl:.1f}\n"
        f"TSB (balance): {state.tsb:.1f}\n\n"
        "CTL shows long-term fitness, ATL reflects recent load, "
        "and TSB indicates readiness."
    )
