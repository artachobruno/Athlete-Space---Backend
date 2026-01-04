from loguru import logger

from app.coach.models import AthleteState


def recommend_next_session(state: AthleteState) -> str:
    """Recommend today's session based on fatigue and load balance."""
    logger.info(f"Tool recommend_next_session called (TSB={state.tsb:.1f}, CTL={state.ctl:.1f}, confidence={state.confidence:.2f})")

    # Check confidence - ask clarifying questions with insufficient data
    if state.confidence < 0.1:
        return (
            "I'd love to recommend today's session! To give you the best guidance, could you tell me:\n\n"
            "• How are you feeling today? (tired, fresh, or somewhere in between?)\n"
            "• What did you do yesterday? (rest day, easy run, or harder workout?)\n"
            "• What's your goal for today? (easy recovery, moderate effort, or quality session?)\n\n"
            "Once I know this, I can recommend the perfect session for you. "
            "Also, syncing your Strava activities will help me provide even more personalized recommendations!"
        )

    tsb = state.tsb

    if tsb < -15:
        return (
            "You are in a deep fatigue state.\n\n"
            "✅ Recommended session:\n"
            "- Rest day OR 30-40 min very easy aerobic\n"
            "- HR Z1 only\n"
            "- Focus on sleep and nutrition"
        )

    if tsb < -8:
        return (
            "Fatigue is elevated.\n\n"
            "✅ Recommended session:\n"
            "- Easy aerobic run 45-70 min\n"
            "- Strides optional (4-6 x 20s)\n"
            "- Avoid workouts today"
        )

    if tsb > 5:
        return (
            "You are fresh and absorbing training well.\n\n"
            "✅ Recommended session:\n"
            "- Quality workout day\n"
            "- Threshold or VO₂ session depending on plan\n"
            "- Total load ≤ 1.2 x ATL"
        )

    return (
        "Training load is balanced.\n\n✅ Recommended session:\n- Steady aerobic run 60-90 min\n- Optional light progression in last 20 min"
    )
