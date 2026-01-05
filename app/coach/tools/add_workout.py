from datetime import datetime, timedelta, timezone

from loguru import logger

from app.coach.models import AthleteState
from app.coach.tools.session_planner import save_planned_sessions


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


def _parse_workout_details(workout_lower: str) -> tuple[str, str, int, str]:
    """Parse workout description to extract type, title, duration, and intensity.

    Args:
        workout_lower: Lowercase workout description

    Returns:
        Tuple of (title, intensity, duration_minutes, type)
    """
    # Determine workout type (default to Run)
    workout_type = "Run"
    if "bike" in workout_lower or "cycling" in workout_lower:
        workout_type = "Bike"
    elif "swim" in workout_lower:
        workout_type = "Swim"

    # Workout patterns with priority order (more specific first)
    workout_patterns = [
        # Interval variations (most specific first)
        (["interval", "repetition", "vo2"], ("VO₂max Intervals", "hard", 70)),
        (["interval", "repetition", "5k"], ("VO₂max Intervals", "hard", 70)),
        (["interval", "repetition", "3k"], ("VO₂max Intervals", "hard", 70)),
        (["interval", "repetition", "threshold"], ("Threshold Intervals", "hard", 80)),
        (["interval", "repetition", "tempo"], ("Threshold Intervals", "hard", 80)),
        (["interval", "repetition"], ("Intervals", "hard", 60)),
        # Other workout types
        (["tempo"], ("Tempo Run", "moderate", 70)),
        (["threshold"], ("Tempo Run", "moderate", 70)),
        (["long"], ("Long Run", "easy", 90)),
        (["endurance"], ("Long Run", "easy", 90)),
        (["easy"], ("Easy Run", "easy", 60)),
        (["recovery"], ("Easy Run", "easy", 60)),
        (["aerobic"], ("Easy Run", "easy", 60)),
        (["fartlek"], ("Fartlek", "moderate", 55)),
    ]

    # Check patterns in order
    for keywords, (title, intensity, duration) in workout_patterns:
        if any(keyword in workout_lower for keyword in keywords):
            return (title, intensity, duration, workout_type)

    # Default
    return ("Workout", "moderate", 60, workout_type)


def _extract_date_from_description(workout_lower: str) -> datetime | None:
    """Extract date from workout description.

    Args:
        workout_lower: Lowercase workout description

    Returns:
        datetime object if date found, None otherwise
    """
    # Check for "today", "tomorrow", day names
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    if "today" in workout_lower:
        return today
    if "tomorrow" in workout_lower:
        return today + timedelta(days=1)

    # Check for day names (monday, tuesday, etc.)
    days = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for day_name, day_offset in days.items():
        if day_name in workout_lower:
            current_weekday = today.weekday()
            target_offset = day_offset
            days_ahead = (target_offset - current_weekday) % 7
            if days_ahead == 0:
                days_ahead = 7  # Next week if same day
            return today + timedelta(days=days_ahead)

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


def add_workout(
    state: AthleteState,
    workout_description: str,
    user_id: str | None = None,
    athlete_id: int | None = None,
) -> str:
    """Add a specific workout to the training plan.

    Args:
        state: Current athlete state.
        workout_description: User's description of the workout they want to add.
        user_id: Optional user ID for saving to calendar.
        athlete_id: Optional athlete ID for saving to calendar.

    Returns:
        Confirmation and guidance on adding the workout to the plan.
    """
    logger.info(f"Tool add_workout called (description_length={len(workout_description)}, TSB={state.tsb:.1f})")
    tsb = state.tsb
    workout_lower = workout_description.lower()

    # Check if athlete is too fatigued for hard workouts
    fatigue_warning = _check_fatigue_warning(tsb, workout_lower)
    if fatigue_warning:
        return fatigue_warning

    # Parse workout type
    recommendation = _parse_workout_type(workout_lower, tsb, workout_description)

    # Save to calendar if user_id and athlete_id provided
    if user_id and athlete_id:
        try:
            # Extract workout details
            title, intensity, duration_minutes, workout_type = _parse_workout_details(workout_lower)

            # Determine date (default to tomorrow if not specified)
            workout_date = _extract_date_from_description(workout_lower)
            if workout_date is None:
                # Default to tomorrow
                tomorrow = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                workout_date = tomorrow

            # Create session data
            session_data = {
                "date": workout_date,
                "type": workout_type,
                "title": title,
                "duration_minutes": duration_minutes,
                "intensity": intensity,
                "notes": None,
            }

            # Save session
            saved_count = save_planned_sessions(
                user_id=user_id,
                athlete_id=athlete_id,
                sessions=[session_data],
                plan_type="single",
                plan_id=None,
            )

            if saved_count > 0:
                date_str = workout_date.strftime("%B %d, %Y")
                recommendation += f"\n\n✅ Session saved to your calendar for {date_str}!"
            else:
                recommendation += "\n\nNote: Session may already exist in your calendar."

        except Exception as e:
            logger.error(f"Error saving workout to calendar: {e}", exc_info=True)
            recommendation += "\n\n⚠️ Note: Could not save to calendar, but the workout plan is ready!"

    # Add context based on state
    if tsb > 5:
        recommendation += "\n\n💡 You're fresh - good time for quality work!"
    elif tsb < -8:
        recommendation += "\n\n⚠️ Monitor fatigue - consider reducing intensity if feeling tired."

    return recommendation
