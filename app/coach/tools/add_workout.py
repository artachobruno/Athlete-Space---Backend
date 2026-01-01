from app.coach.models import AthleteState


def _check_fatigue_warning(tsb: float, workout_lower: str) -> str | None:
    """Check if athlete is too fatigued for hard workouts."""
    if tsb < -15 and any(keyword in workout_lower for keyword in ["interval", "speed", "tempo", "threshold", "hard", "fast"]):
        return (
            "⚠️ Fatigue Warning\n\n"
            f"Your current TSB is {tsb:.1f}, indicating high fatigue.\n\n"
            "I recommend:\n"
            "- Postponing this hard workout until recovery improves\n"
            "- Converting to an easy aerobic session instead\n"
            "- Adding this workout in 2-3 days once TSB improves above -10\n\n"
            "If you still want to proceed, keep intensity conservative and monitor recovery closely."
        )
    return None


def _get_interval_workout_message(workout_lower: str) -> str | None:
    """Get specific interval workout message based on keywords."""
    if "vo2" in workout_lower or "5k" in workout_lower or "3k" in workout_lower:
        return (
            "✅ VO₂max Interval Workout Added\n\n"
            "Suggested structure:\n"
            "- Warm-up: 15-20 min easy\n"
            "- Main set: 5-6 x 3-4 min @ 3K-5K pace\n"
            "- Recovery: 2-3 min jog between intervals\n"
            "- Cool-down: 10-15 min easy\n\n"
            "Total duration: ~60-75 min\n"
            "Focus: High intensity, controlled form"
        )
    if "threshold" in workout_lower or "tempo" in workout_lower:
        return (
            "✅ Threshold Interval Workout Added\n\n"
            "Suggested structure:\n"
            "- Warm-up: 15-20 min easy\n"
            "- Main set: 3-4 x 8-10 min @ threshold pace\n"
            "- Recovery: 2-3 min jog between intervals\n"
            "- Cool-down: 10-15 min easy\n\n"
            "Total duration: ~75-90 min\n"
            "Focus: Sustained effort, aerobic power"
        )
    return None


def _parse_workout_type(workout_lower: str, tsb: float, workout_description: str) -> str:
    """Parse workout type and return recommendation."""
    # Check for interval/repetition workouts first
    if "interval" in workout_lower or "repetition" in workout_lower:
        interval_msg = _get_interval_workout_message(workout_lower)
        if interval_msg:
            return interval_msg
        return (
            "✅ Interval Workout Added\n\n"
            "Ensure proper warm-up and cool-down.\n"
            f"Adjust intensity based on current fatigue (TSB: {tsb:.1f})."
        )

    # Check other workout types
    workout_patterns = {
        ("tempo", "threshold"): (
            "✅ Tempo Run Added\n\n"
            "Suggested structure:\n"
            "- Warm-up: 15-20 min easy\n"
            "- Main set: 20-30 min continuous @ threshold pace\n"
            "- Cool-down: 10-15 min easy\n\n"
            "Total duration: ~60-75 min\n"
            "Focus: Controlled, sustainable effort"
        ),
        ("long", "endurance"): (
            "✅ Long Run Added\n\n"
            "Suggested structure:\n"
            "- Duration: 90-120 min (adjust based on weekly volume)\n"
            "- Pace: Easy to moderate aerobic (Z2)\n"
            "- Optional: Progressive finish (last 20-30 min slightly faster)\n\n"
            "Focus: Aerobic development, time on feet"
        ),
        ("easy", "recovery", "aerobic"): (
            "✅ Easy Aerobic Run Added\n\nDuration: 45-90 min at easy pace (Z1-2)\nFocus: Recovery, aerobic base building"
        ),
        ("fartlek",): (
            "✅ Fartlek Workout Added\n\n"
            "Suggested structure:\n"
            "- Warm-up: 15 min easy\n"
            "- Main set: 20-30 min fartlek (e.g., 1 min hard / 1 min easy)\n"
            "- Cool-down: 10-15 min easy\n\n"
            "Total duration: ~50-60 min\n"
            "Focus: Variable pace, fun variation"
        ),
    }

    for keywords, message in workout_patterns.items():
        if any(keyword in workout_lower for keyword in keywords):
            return message

    # Default message
    return (
        "✅ Workout Added\n\n"
        f"I've noted your workout request: {workout_description}\n\n"
        "Make sure to include:\n"
        "- Proper warm-up (15-20 min)\n"
        "- Main workout component\n"
        "- Cool-down (10-15 min)\n\n"
        f"Adjust intensity based on your current fatigue level (TSB: {tsb:.1f})."
    )


def add_workout(state: AthleteState, workout_description: str) -> str:
    """Add a specific workout to the training plan.

    Args:
        state: Current athlete state.
        workout_description: User's description of the workout they want to add.

    Returns:
        Confirmation and guidance on adding the workout to the plan.
    """
    tsb = state.tsb
    workout_lower = workout_description.lower()

    # Check if athlete is too fatigued for hard workouts
    fatigue_warning = _check_fatigue_warning(tsb, workout_lower)
    if fatigue_warning:
        return fatigue_warning

    # Parse workout type
    recommendation = _parse_workout_type(workout_lower, tsb, workout_description)

    # Add context based on state
    if tsb > 5:
        recommendation += "\n\n💡 You're fresh - good time for quality work!"
    elif tsb < -8:
        recommendation += "\n\n⚠️ Monitor fatigue - consider reducing intensity if feeling tired."

    return recommendation
