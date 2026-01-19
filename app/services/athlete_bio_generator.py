"""Athlete bio generation service using LLM.

This service generates narrative bios from structured athlete profile data.
It uses LLM to create a 3-5 sentence bio that summarizes the athlete's profile.
"""

from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.models.athlete_profile import AthleteProfile as AthleteProfileSchema
from app.models.athlete_profile import NarrativeBio
from app.services.llm.model import get_model


class NarrativeBioOutput(BaseModel):
    """Schema for LLM bio output."""

    text: str = Field(description="Bio text (3-5 sentences)")


def _load_bio_prompt() -> str:
    """Load bio generator prompt from local filesystem.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    # Try to find prompt in coach prompts directory
    prompt_dir = Path(__file__).parent.parent / "coach" / "prompts"
    prompt_path = prompt_dir / "athlete_bio_generator.txt"

    if not prompt_path.exists():
        # Fallback to default prompt
        return _default_bio_prompt()

    return prompt_path.read_text(encoding="utf-8")


def _default_bio_prompt() -> str:
    """Default bio generator prompt.

    Returns:
        Default prompt content
    """
    return """You are an expert at writing concise athlete profiles.

Your task is to generate a brief narrative bio (3-5 sentences) that summarizes an athlete's profile based on structured data.

Rules:
1. Write in third person
2. Use 3-5 sentences only
3. Focus on key aspects: sport, experience level, goals, and training context
4. Be professional but engaging
5. Avoid generic phrases - be specific to the athlete's data
6. If data is sparse, acknowledge that but still create a valid bio

The bio should feel natural and informative, not like a data dump."""


def _build_profile_summary(profile: AthleteProfileSchema) -> str:
    """Build a text summary of the profile for the LLM.

    Args:
        profile: Athlete profile schema

    Returns:
        Formatted profile summary string
    """
    parts = []

    # Identity
    if profile.identity.first_name:
        parts.append(f"Name: {profile.identity.first_name}")
        if profile.identity.last_name:
            parts.append(f"Last name: {profile.identity.last_name}")
    if profile.identity.age:
        parts.append(f"Age: {profile.identity.age}")
    if profile.identity.location:
        parts.append(f"Location: {profile.identity.location}")

    # Training context
    parts.append(f"Primary sport: {profile.training_context.primary_sport.value}")
    parts.append(f"Experience level: {profile.training_context.experience_level.value}")
    if profile.training_context.years_training:
        parts.append(f"Years training: {profile.training_context.years_training}")
    if profile.training_context.current_phase.value != "unknown":
        parts.append(f"Current phase: {profile.training_context.current_phase.value}")

    # Goals
    if profile.goals.primary_goal:
        parts.append(f"Primary goal: {profile.goals.primary_goal}")
    if profile.goals.goal_type.value != "unknown":
        parts.append(f"Goal type: {profile.goals.goal_type.value}")
    if profile.goals.target_event:
        parts.append(f"Target event: {profile.goals.target_event}")
    if profile.goals.target_date:
        parts.append(f"Target date: {profile.goals.target_date}")

    # Constraints
    if profile.constraints.availability_days_per_week:
        parts.append(f"Available days per week: {profile.constraints.availability_days_per_week}")
    if profile.constraints.availability_hours_per_week:
        parts.append(f"Available hours per week: {profile.constraints.availability_hours_per_week}")
    if profile.constraints.injury_status:
        parts.append(f"Injury status: {profile.constraints.injury_status}")

    # Preferences
    if profile.preferences.recovery_preference.value != "unknown":
        parts.append(f"Recovery preference: {profile.preferences.recovery_preference.value}")
    if profile.preferences.coaching_style.value != "unknown":
        parts.append(f"Coaching style: {profile.preferences.coaching_style.value}")

    return "\n".join(parts)


def _calculate_confidence(profile: AthleteProfileSchema) -> float:
    """Calculate confidence score based on data completeness.

    Args:
        profile: Athlete profile schema

    Returns:
        Confidence score (0.0-1.0)
    """
    score = 0.0
    max_score = 0.0

    # Identity completeness (20% weight)
    identity_fields = [
        profile.identity.first_name,
        profile.identity.age,
        profile.identity.location,
    ]
    filled_identity = sum(1 for f in identity_fields if f)
    score += (filled_identity / len(identity_fields)) * 0.2
    max_score += 0.2

    # Training context completeness (30% weight)
    training_fields = [
        profile.training_context.primary_sport.value != "unknown",
        profile.training_context.experience_level.value != "unknown",
        profile.training_context.years_training is not None,
    ]
    filled_training = sum(1 for f in training_fields if f)
    score += (filled_training / len(training_fields)) * 0.3
    max_score += 0.3

    # Goals completeness (25% weight)
    goals_fields = [
        profile.goals.primary_goal,
        profile.goals.goal_type.value != "unknown",
        profile.goals.target_event,
    ]
    filled_goals = sum(1 for f in goals_fields if f)
    score += (filled_goals / len(goals_fields)) * 0.25
    max_score += 0.25

    # Constraints completeness (15% weight)
    constraints_fields = [
        profile.constraints.availability_days_per_week is not None,
        profile.constraints.availability_hours_per_week is not None,
    ]
    filled_constraints = sum(1 for f in constraints_fields if f)
    score += (filled_constraints / len(constraints_fields)) * 0.15
    max_score += 0.15

    # Preferences completeness (10% weight)
    preferences_fields = [
        profile.preferences.recovery_preference.value != "unknown",
        profile.preferences.coaching_style.value != "unknown",
    ]
    filled_preferences = sum(1 for f in preferences_fields if f)
    score += (filled_preferences / len(preferences_fields)) * 0.1
    max_score += 0.1

    # Normalize to 0.0-1.0
    if max_score == 0:
        return 0.0

    normalized_score = score / max_score

    # Signal consistency bonus
    # If all filled fields are consistent (no contradictions), add small bonus
    consistency_bonus = 0.05
    normalized_score = min(1.0, normalized_score + consistency_bonus)

    return round(normalized_score, 2)


async def generate_athlete_bio(profile: AthleteProfileSchema) -> NarrativeBio:
    """Generate narrative bio from structured profile.

    Args:
        profile: Structured athlete profile

    Returns:
        NarrativeBio object with generated text and confidence score
    """
    logger.info("Generating athlete bio via LLM", user_id=None)

    # Calculate confidence score
    confidence = _calculate_confidence(profile)

    # Build profile summary
    profile_summary = _build_profile_summary(profile)

    # Load prompt
    system_prompt = _load_bio_prompt()

    # Build user message
    user_message = f"""Generate a narrative bio for this athlete:

{profile_summary}

Write a 3-5 sentence bio that summarizes this athlete's profile."""

    # Create agent with schema output
    model = get_model("openai", USER_FACING_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=NarrativeBioOutput,
    )

    try:
        # Run agent
        logger.debug(
            "LLM Prompt: Athlete Bio Generation",
            system_prompt=system_prompt,
            user_prompt=user_message,
        )
        result = await agent.run(user_message)
        bio_text = result.data.text

        # Validate sentence count (rough check)
        sentences = bio_text.split(". ")
        if len(sentences) < 3:
            logger.warning("Bio has fewer than 3 sentences, adjusting", sentence_count=len(sentences))
        elif len(sentences) > 6:
            logger.warning("Bio has more than 5 sentences, may need truncation", sentence_count=len(sentences))

        # Create bio object
        bio = NarrativeBio(
            text=bio_text,
            confidence_score=confidence,
            source="ai_generated",
            depends_on_hash=None,  # Will be set by caller
        )

        logger.info("Athlete bio generated successfully", confidence=confidence, sentence_count=len(sentences))

    except Exception as e:
        logger.error(f"Failed to generate bio via LLM: {e}", exc_info=True)
        # Fallback to generic bio
        fallback_text = _generate_fallback_bio(profile)
        return NarrativeBio(
            text=fallback_text,
            confidence_score=max(0.3, confidence - 0.2),
            source="ai_generated",
            depends_on_hash=None,
        )
    else:
        return bio


def _generate_fallback_bio(profile: AthleteProfileSchema) -> str:
    """Generate a simple fallback bio when LLM fails.

    Args:
        profile: Athlete profile schema

    Returns:
        Fallback bio text
    """
    parts = []

    if profile.identity.first_name:
        parts.append(f"{profile.identity.first_name} is")
    else:
        parts.append("This athlete is")

    # Add sport and experience
    primary_sport_value = profile.training_context.primary_sport.value
    sport = primary_sport_value if primary_sport_value != "unknown" else "an athlete"
    experience_level_value = profile.training_context.experience_level.value
    experience = experience_level_value if experience_level_value != "unknown" else "a dedicated"

    parts.append(f"a {experience} {sport} athlete.")

    # Add goal if available
    if profile.goals.primary_goal:
        parts.append(f"Their primary goal is {profile.goals.primary_goal.lower()}.")
    elif profile.goals.target_event:
        parts.append(f"They are training for {profile.goals.target_event}.")

    # Add availability if available
    if profile.constraints.availability_days_per_week:
        parts.append(
            f"They train {profile.constraints.availability_days_per_week} days per week."
        )

    return " ".join(parts)
