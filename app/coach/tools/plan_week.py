from loguru import logger

from app.coach.models import AthleteState


def plan_week(state: AthleteState) -> str:
    """Generate a weekly training plan based on current state.

    Args:
        state: Current athlete state with load metrics and trends.

    Returns:
        A weekly training plan tailored to the athlete's current state.
    """
    logger.info(f"Tool plan_week called (TSB={state.tsb:.1f}, load_trend={state.load_trend}, flags={state.flags})")
    tsb = state.tsb
    load_trend = state.load_trend
    flags = state.flags

    # High fatigue - recovery week
    if tsb < -12 or "OVERREACHING" in flags:
        return (
            "📅 Recovery Week Plan\n\n"
            "Monday: Rest day\n"
            "Tuesday: Easy 30-40 min aerobic (Z1-2)\n"
            "Wednesday: Rest or easy 20-30 min\n"
            "Thursday: Easy 45-60 min aerobic (Z1-2)\n"
            "Friday: Rest day\n"
            "Saturday: Easy 60-75 min aerobic with strides (4-6 x 20s)\n"
            "Sunday: Easy 45-60 min aerobic\n\n"
            "Total volume: 40-50% of normal week\n"
            "Focus: Recovery, sleep, nutrition"
        )

    # Fresh and ready - build week
    if tsb > 5 and load_trend == "stable":
        return (
            "📅 Build Week Plan\n\n"
            "Monday: Rest or easy 30 min\n"
            "Tuesday: Quality - Threshold intervals (3-4 x 8-10 min @ threshold)\n"
            "Wednesday: Easy 60-75 min aerobic (Z2)\n"
            "Thursday: Quality - VO₂max intervals (5-6 x 3-4 min @ 3K pace)\n"
            "Friday: Easy 45 min recovery\n"
            "Saturday: Long run 90-120 min (progressive if feeling good)\n"
            "Sunday: Easy 60-90 min aerobic\n\n"
            "Total: 2 quality sessions, 1 long run, ~12-15 hours\n"
            "Focus: Controlled intensity, maintain volume"
        )

    # Moderate load - balanced week
    if -8 <= tsb <= 5:
        return (
            "📅 Balanced Week Plan\n\n"
            "Monday: Easy 45-60 min aerobic\n"
            "Tuesday: Quality - Tempo run (20-30 min @ threshold)\n"
            "Wednesday: Easy 60-75 min aerobic\n"
            "Thursday: Easy 60-90 min aerobic\n"
            "Friday: Rest or easy 30-40 min\n"
            "Saturday: Long run 75-90 min steady\n"
            "Sunday: Easy 60-75 min aerobic\n\n"
            "Total: 1 quality session, 1 long run, ~10-12 hours\n"
            "Focus: Maintain consistency, monitor fatigue"
        )

    # Falling load - need to rebuild
    if load_trend == "falling" and tsb > 0:
        return (
            "📅 Rebuild Week Plan\n\n"
            "Monday: Easy 60 min aerobic\n"
            "Tuesday: Moderate - Tempo progression (15 min @ threshold)\n"
            "Wednesday: Easy 60-75 min aerobic\n"
            "Thursday: Easy 75-90 min aerobic\n"
            "Friday: Rest or easy 30 min\n"
            "Saturday: Long run 90-105 min steady\n"
            "Sunday: Easy 75-90 min aerobic\n\n"
            "Total: 1 moderate session, 1 long run, ~12-14 hours\n"
            "Focus: Gradually increase volume, add intensity next week"
        )

    # Default balanced plan
    return (
        "📅 Standard Week Plan\n\n"
        "Monday: Easy 45-60 min\n"
        "Tuesday: Quality session (threshold or intervals)\n"
        "Wednesday: Easy 60-75 min\n"
        "Thursday: Easy 60-90 min\n"
        "Friday: Rest or easy 30-40 min\n"
        "Saturday: Long run 75-90 min\n"
        "Sunday: Easy 60-75 min\n\n"
        "Total: 1-2 quality sessions, 1 long run\n"
        "Adjust based on your weekly volume target and recovery needs."
    )
