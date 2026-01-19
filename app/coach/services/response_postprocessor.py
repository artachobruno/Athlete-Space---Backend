"""Response postprocessor for coach responses.

This module handles post-processing of coach responses, including:
- Adding profile-based explanations
- Ensuring explanations appear only once per response
"""

from loguru import logger

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.schemas.orchestrator_response import ResponseType


def should_add_profile_explanation(
    response_type: ResponseType,
    message: str,
    deps: CoachDeps,
    confidence: float,
) -> bool:
    """Determine if profile explanation should be added.

    Args:
        response_type: Type of response
        message: Current message content
        deps: Coach dependencies
        confidence: Response confidence

    Returns:
        True if profile explanation should be added
    """
    # Don't add for greetings or questions
    if response_type in {"greeting", "question"}:
        return False

    # Don't add if confidence is too low
    if confidence < 0.6:
        return False

    # Don't add if already explained
    if "based on your profile" in message.lower():
        return False

    # Only add if structured profile data exists
    return deps.structured_profile_data is not None


def build_profile_explanation(deps: CoachDeps) -> str:
    """Build profile-based explanation line.

    Args:
        deps: Coach dependencies

    Returns:
        Explanation line (e.g., "Based on your profile...")
    """
    if not deps.structured_profile_data:
        return "Based on your profile and recent training…"

    profile_data = deps.structured_profile_data
    constraint_parts = []

    # Check constraints (availability)
    if profile_data.constraints:
        constraints = profile_data.constraints
        days = constraints.get("availability_days_per_week")
        hours = constraints.get("availability_hours_per_week")

        if days and hours:
            constraint_parts.append(f"{days} days per week")
            constraint_parts.append(f"{hours} hours per week")
        elif days:
            constraint_parts.append(f"{days} days per week")
        elif hours:
            constraint_parts.append(f"{hours} hours per week")

        # Check recovery preference from preferences
        if profile_data.structured_profile:
            preferences = profile_data.structured_profile.get("preferences", {})
            recovery_pref = preferences.get("recovery_preference", "unknown")
            if recovery_pref and recovery_pref != "unknown":
                constraint_parts.append(f"{recovery_pref} recovery preference")

        if constraint_parts:
            return f"Based on your profile — especially your {', '.join(constraint_parts)} —"

    # Check goals
    if profile_data.structured_profile:
        goals = profile_data.structured_profile.get("goals", {})
        goal_type = goals.get("goal_type", "unknown")
        target_event = goals.get("target_event")

        if goal_type and goal_type != "unknown":
            if target_event:
                return f"Based on your {goal_type}-focused {target_event} goal —"
            return f"Based on your {goal_type} goal —"

    # Default
    return "Based on your profile and recent training…"


def postprocess_response(
    message: str,
    response_type: ResponseType,
    deps: CoachDeps,
    confidence: float,
) -> str:
    """Post-process coach response.

    Adds profile-based explanation if appropriate.

    Args:
        message: Original message
        response_type: Response type
        deps: Coach dependencies
        confidence: Response confidence

    Returns:
        Post-processed message
    """
    if not should_add_profile_explanation(response_type, message, deps, confidence):
        return message

    explanation = build_profile_explanation(deps)

    # Prepend explanation to message
    if message.strip().startswith(explanation.split("—")[0].strip()):
        # Already has explanation prefix, don't duplicate
        return message

    return f"{explanation} {message}"
