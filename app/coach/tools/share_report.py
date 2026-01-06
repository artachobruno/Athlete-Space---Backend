from datetime import datetime, timezone

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState


def share_report(state: AthleteState) -> str:
    """Return training state data for report generation.

    Args:
        state: Current athlete state with all metrics.

    Returns:
        Training state data or clarification request
    """
    logger.info(f"Tool share_report called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, TSB={state.tsb:.1f})")

    if state.confidence < 0.1:
        return "[CLARIFICATION] athlete_state_confidence_low"

    report_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # Return structured data for orchestrator to format
    state_data = (
        f"Date: {report_date}, "
        f"CTL: {state.ctl:.1f}, ATL: {state.atl:.1f}, TSB: {state.tsb:.1f}, "
        f"Load trend: {state.load_trend}, Volatility: {state.volatility}, "
        f"Days since rest: {state.days_since_rest}, "
        f"7-day volume: {state.seven_day_volume_hours:.1f}h, "
        f"14-day volume: {state.fourteen_day_volume_hours:.1f}h"
    )
    if state.flags:
        state_data += f", Flags: {', '.join(state.flags)}"
    if state.days_to_race:
        state_data += f", Days to race: {state.days_to_race}"
    return state_data
