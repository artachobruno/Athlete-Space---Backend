"""Hard guardrails for LLM-generated training intents.

These are validation rules that constrain LLM outputs.
They do NOT generate outputs - they only validate them.
"""

from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent

# Volume bounds (hours per week)
MAX_WEEKLY_VOLUME_HOURS = 30.0
MIN_WEEKLY_VOLUME_HOURS = 0.0

# Daily volume bounds (hours per day)
MAX_DAILY_VOLUME_HOURS = 6.0
MIN_DAILY_VOLUME_HOURS = 0.0

# Week-over-week volume change limits (percentage)
MAX_WEEK_OVER_WEEK_INCREASE_PCT = 20.0
MAX_WEEK_OVER_WEEK_DECREASE_PCT = 30.0

# Confidence score bounds
MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0

# Explanation length limits (characters)
MIN_EXPLANATION_LENGTH = 50
MAX_EXPLANATION_LENGTH = 1000

# Season plan explanation length
MIN_SEASON_EXPLANATION_LENGTH = 100
MAX_SEASON_EXPLANATION_LENGTH = 1000

# Weekly intent explanation length
MIN_WEEKLY_EXPLANATION_LENGTH = 100
MAX_WEEKLY_EXPLANATION_LENGTH = 1000

# Daily decision explanation length
MIN_DAILY_EXPLANATION_LENGTH = 50
MAX_DAILY_EXPLANATION_LENGTH = 500

# Allowed recommendation types for daily decisions (must match DailyDecision schema and daily_decision.txt)
ALLOWED_DAILY_RECOMMENDATIONS = {
    "rest",
    "easy",
    "easy_with_caution",
    "moderate",
    "moderate_with_caution",
    "hard",
    "race",
}

# Allowed risk levels
ALLOWED_RISK_LEVELS = {"none", "low", "medium", "high"}


def validate_season_plan(plan: SeasonPlan) -> list[str]:
    """Validate a SeasonPlan against hard constraints.

    Args:
        plan: The season plan to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    errors: list[str] = []

    # Confidence bounds
    if not (MIN_CONFIDENCE <= plan.confidence <= MAX_CONFIDENCE):
        errors.append(f"Confidence must be between {MIN_CONFIDENCE} and {MAX_CONFIDENCE}")

    # Explanation length
    if not (MIN_SEASON_EXPLANATION_LENGTH <= len(plan.explanation) <= MAX_SEASON_EXPLANATION_LENGTH):
        errors.append(f"Explanation must be between {MIN_SEASON_EXPLANATION_LENGTH} and {MAX_SEASON_EXPLANATION_LENGTH} characters")

    # Date validation
    if plan.season_start >= plan.season_end:
        errors.append("Season start date must be before season end date")

    # Season length (reasonable bounds: 4-52 weeks)
    days_diff = (plan.season_end - plan.season_start).days
    if days_diff < 28 or days_diff > 365:
        errors.append("Season length must be between 4 and 52 weeks")

    return errors


def validate_weekly_intent(intent: WeeklyIntent, previous_volume: float | None = None) -> list[str]:
    """Validate a WeeklyIntent against hard constraints.

    Args:
        intent: The weekly intent to validate
        previous_volume: Previous week's volume in hours (for week-over-week validation)

    Returns:
        List of validation error messages (empty if valid)
    """
    errors: list[str] = []

    # Volume bounds
    if not (MIN_WEEKLY_VOLUME_HOURS <= intent.volume_target_hours <= MAX_WEEKLY_VOLUME_HOURS):
        errors.append(f"Volume target must be between {MIN_WEEKLY_VOLUME_HOURS} and {MAX_WEEKLY_VOLUME_HOURS} hours")

    # Confidence bounds
    if not (MIN_CONFIDENCE <= intent.confidence <= MAX_CONFIDENCE):
        errors.append(f"Confidence must be between {MIN_CONFIDENCE} and {MAX_CONFIDENCE}")

    # Explanation length
    if not (MIN_WEEKLY_EXPLANATION_LENGTH <= len(intent.explanation) <= MAX_WEEKLY_EXPLANATION_LENGTH):
        errors.append(f"Explanation must be between {MIN_WEEKLY_EXPLANATION_LENGTH} and {MAX_WEEKLY_EXPLANATION_LENGTH} characters")

    # Week-over-week volume change
    if previous_volume is not None and previous_volume > 0:
        change_pct = ((intent.volume_target_hours - previous_volume) / previous_volume) * 100.0
        if change_pct > MAX_WEEK_OVER_WEEK_INCREASE_PCT:
            errors.append(f"Week-over-week volume increase ({change_pct:.1f}%) exceeds maximum ({MAX_WEEK_OVER_WEEK_INCREASE_PCT}%)")
        if change_pct < -MAX_WEEK_OVER_WEEK_DECREASE_PCT:
            errors.append(f"Week-over-week volume decrease ({abs(change_pct):.1f}%) exceeds maximum ({MAX_WEEK_OVER_WEEK_DECREASE_PCT}%)")

    # Week number validation
    if intent.week_number < 1:
        errors.append("Week number must be at least 1")

    return errors


def _validate_daily_recommendation(decision: DailyDecision) -> list[str]:
    """Validate recommendation field."""
    errors: list[str] = []
    if decision.recommendation not in ALLOWED_DAILY_RECOMMENDATIONS:
        errors.append(f"Recommendation must be one of: {', '.join(ALLOWED_DAILY_RECOMMENDATIONS)}")
    return errors


def _validate_daily_volume(decision: DailyDecision) -> list[str]:
    """Validate volume field."""
    errors: list[str] = []
    if decision.recommendation != "rest":
        if decision.volume_hours is None:
            errors.append("Volume hours must be provided for non-rest recommendations")
        elif not (MIN_DAILY_VOLUME_HOURS <= decision.volume_hours <= MAX_DAILY_VOLUME_HOURS):
            errors.append(f"Volume hours must be between {MIN_DAILY_VOLUME_HOURS} and {MAX_DAILY_VOLUME_HOURS} hours")
    elif decision.volume_hours is not None:
        errors.append("Volume hours must be null for rest days")
    return errors


def _validate_daily_risk(decision: DailyDecision) -> list[str]:
    """Validate risk fields."""
    errors: list[str] = []
    if decision.risk_level not in ALLOWED_RISK_LEVELS:
        errors.append(f"Risk level must be one of: {', '.join(ALLOWED_RISK_LEVELS)}")
    if decision.risk_level == "none" and decision.risk_notes is not None:
        errors.append("Risk notes must be null when risk level is 'none'")
    if decision.risk_level != "none" and decision.risk_notes is None:
        errors.append("Risk notes must be provided when risk level is not 'none'")
    return errors


def _validate_daily_session_fields(decision: DailyDecision) -> list[str]:
    """Validate session-related fields."""
    errors: list[str] = []
    if decision.recommendation == "rest":
        if decision.intensity_focus is not None:
            errors.append("Intensity focus must be null for rest days")
        if decision.session_type is not None:
            errors.append("Session type must be null for rest days")
    else:
        if decision.intensity_focus is None:
            errors.append("Intensity focus must be provided for non-rest recommendations")
        if decision.session_type is None:
            errors.append("Session type must be provided for non-rest recommendations")
    return errors


def validate_daily_decision(decision: DailyDecision) -> list[str]:
    """Validate a DailyDecision against hard constraints.

    Args:
        decision: The daily decision to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    errors: list[str] = []

    errors.extend(_validate_daily_recommendation(decision))
    errors.extend(_validate_daily_volume(decision))
    errors.extend(_validate_daily_risk(decision))

    # Confidence bounds
    if not (MIN_CONFIDENCE <= decision.confidence.score <= MAX_CONFIDENCE):
        errors.append(f"Confidence score must be between {MIN_CONFIDENCE} and {MAX_CONFIDENCE}")

    # Confidence explanation length
    if not (20 <= len(decision.confidence.explanation) <= 200):
        errors.append("Confidence explanation must be between 20 and 200 characters")

    # Explanation length
    if not (MIN_DAILY_EXPLANATION_LENGTH <= len(decision.explanation) <= MAX_DAILY_EXPLANATION_LENGTH):
        errors.append(f"Explanation must be between {MIN_DAILY_EXPLANATION_LENGTH} and {MAX_DAILY_EXPLANATION_LENGTH} characters")

    errors.extend(_validate_daily_session_fields(decision))

    return errors
