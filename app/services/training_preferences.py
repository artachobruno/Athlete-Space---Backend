"""Service for managing training preferences and race extraction."""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.orm import Session

from app.db.models import AthleteProfile, StravaAccount, UserSettings
from app.onboarding.extraction import ExtractedRaceAttributes, GoalExtractionService


def extract_and_store_race_info(
    session: Session,
    user_id: str,
    settings: UserSettings,
    profile: AthleteProfile | None = None,
) -> dict[str, str | None] | None:
    """Extract race information from training preferences and store in profile.

    Args:
        session: Database session
        user_id: User ID
        settings: UserSettings with training preferences
        profile: Optional AthleteProfile (will be fetched if not provided)

    Returns:
        Extracted race attributes dict or None if no race info found
    """
    if not profile:
        profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
    if not profile:
        logger.info(f"No profile found for user_id={user_id}, creating one for race extraction")
        strava_account = session.query(StravaAccount).filter_by(user_id=user_id).first()
        athlete_id = int(strava_account.athlete_id) if strava_account else 0
        profile = AthleteProfile(user_id=user_id, athlete_id=athlete_id, sources={})
        session.add(profile)

    # Collect goal text from various sources
    goal_texts = []

    # From settings goal field (primary_training_goal)
    if settings.goal:
        goal_texts.append(settings.goal)

    # From profile goals array
    if profile.goals:
        goal_texts.extend(profile.goals)

    # From target_event if available
    if profile.target_event and isinstance(profile.target_event, dict):
        event_name = profile.target_event.get("name", "")
        event_date = profile.target_event.get("date", "")
        if event_name or event_date:
            goal_texts.append(f"{event_name} {event_date}".strip())

    if not goal_texts:
        logger.info(f"No goal text found for race extraction for user_id={user_id}")
        # Clear existing extracted attributes if no goals
        if profile.extracted_race_attributes:
            profile.extracted_race_attributes = None
            profile.updated_at = datetime.now(timezone.utc)
            session.commit()
        return None

    # Combine all goal texts
    combined_goal_text = " ".join(goal_texts)
    logger.info(f"Extracting race attributes from combined goal text for user_id={user_id}")

    # Extract attributes using LLM
    extraction_service = GoalExtractionService()
    try:
        extracted = extraction_service.extract_race_attributes(combined_goal_text)
    except Exception as e:
        logger.exception(f"Failed to extract race attributes for user_id={user_id}: {e}")
        return None

    # Only store if we found meaningful race information
    if not extracted.event_type and not extracted.event_date:
        logger.info(f"No meaningful race information extracted for user_id={user_id}")
        if profile.extracted_race_attributes:
            profile.extracted_race_attributes = None
            profile.updated_at = datetime.now(timezone.utc)
            session.commit()
        return None

    # Build race profile dict
    race_profile = {
        "event_name": extracted.event_type or None,
        "event_date": extracted.event_date or None,
        "event_type": extracted.event_type or None,
        "target_time": extracted.goal_time or None,
        "distance": extracted.distance or None,
        "location": extracted.location or None,
        "source": "llm_extracted",
        "raw_text": combined_goal_text,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }

    # Store in profile
    profile.extracted_race_attributes = race_profile
    profile.updated_at = datetime.now(timezone.utc)
    session.commit()

    logger.info(
        f"Race info extracted and stored for user_id={user_id}: event_type={extracted.event_type}, event_date={extracted.event_date}"
    )

    return race_profile


def should_trigger_race_extraction(
    old_settings: UserSettings | None,
    new_settings: UserSettings,
) -> bool:
    """Determine if race extraction should be triggered based on changed fields.

    Args:
        old_settings: Previous UserSettings or None
        new_settings: New UserSettings

    Returns:
        True if race extraction should be triggered
    """
    if not old_settings:
        # New settings - extract if goal is provided
        return bool(new_settings.goal)

    # Check if goal field changed
    return old_settings.goal != new_settings.goal
