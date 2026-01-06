"""Build AthleteState from training load data for Coach Agent input."""

from typing import Literal

from app.coach.schemas.athlete_state import AthleteState


def build_athlete_state(
    *,
    ctl: float,
    atl: float,
    tsb: float,
    daily_load: list[float],
    days_to_race: int | None = None,
) -> AthleteState:
    """Build AthleteState from training load metrics.

    Args:
        ctl: Current Chronic Training Load
        atl: Current Acute Training Load
        tsb: Current Training Stress Balance
        daily_load: List of daily training hours
        days_to_race: Optional days until next race

    Returns:
        AthleteState ready for Coach Agent
    """
    # Calculate trends
    load_trend = _calculate_load_trend(daily_load)
    volatility = _calculate_volatility(daily_load)

    # Calculate days since rest (last day with < 0.5 hours)
    days_since_rest = _calculate_days_since_rest(daily_load)

    # Calculate aggregates
    seven_day_volume = sum(daily_load[-7:]) if len(daily_load) >= 7 else sum(daily_load)
    fourteen_day_volume = sum(daily_load[-14:]) if len(daily_load) >= 14 else sum(daily_load)

    # Generate flags from rules
    flags = _generate_flags(ctl, atl, tsb, daily_load, load_trend)

    # Calculate confidence (based on data availability)
    confidence = min(1.0, len(daily_load) / 30.0)  # Full confidence at 30+ days

    return AthleteState(
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        load_trend=load_trend,
        volatility=volatility,
        days_since_rest=days_since_rest,
        days_to_race=days_to_race,
        seven_day_volume_hours=round(seven_day_volume, 1),
        fourteen_day_volume_hours=round(fourteen_day_volume, 1),
        flags=flags,
        confidence=round(confidence, 2),
    )


def _calculate_load_trend(daily_load: list[float]) -> Literal["rising", "stable", "falling"]:
    """Determine load trend from recent data."""
    if len(daily_load) < 7:
        return "stable"

    recent_avg = sum(daily_load[-7:]) / 7
    prev_avg = sum(daily_load[-14:-7]) / 7 if len(daily_load) >= 14 else recent_avg

    if recent_avg > prev_avg * 1.1:
        return "rising"
    if recent_avg < prev_avg * 0.9:
        return "falling"
    return "stable"


def _calculate_volatility(daily_load: list[float]) -> Literal["low", "medium", "high"]:
    """Calculate volatility from daily load variance."""
    if len(daily_load) < 7:
        return "medium"

    mean_load = sum(daily_load) / len(daily_load)
    variance = sum((x - mean_load) ** 2 for x in daily_load) / len(daily_load)
    std_dev = variance**0.5

    if std_dev < mean_load * 0.3:
        return "low"
    if std_dev > mean_load * 0.7:
        return "high"
    return "medium"


def _calculate_days_since_rest(daily_load: list[float]) -> int:
    """Calculate days since last rest day (< 0.5 hours)."""
    if not daily_load:
        return 0

    # Count backwards from most recent
    for i in range(len(daily_load) - 1, -1, -1):
        if daily_load[i] < 0.5:
            return len(daily_load) - 1 - i

    # No rest day found in data
    return len(daily_load)


def _generate_flags(
    _ctl: float,
    _atl: float,
    _tsb: float,
    _daily_load: list[float],
    _load_trend: str,
) -> list[str]:
    """Generate contextual flags for the athlete state.

    Note: Flag generation is now handled by LLM-based coach tools.
    This function returns an empty list to maintain API compatibility.
    Parameters are prefixed with underscore to indicate they are unused.
    """
    return []
