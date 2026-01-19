"""Athlete profile regeneration engine.

This service handles automatic bio regeneration when profile data changes.
It determines when bios need to be regenerated based on trigger fields.
"""

import asyncio
import concurrent.futures
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlalchemy.orm import Session

from app.db.models import AthleteBio, AthleteProfile
from app.models.athlete_profile import AthleteProfile as AthleteProfileSchema
from app.services.athlete_bio_generator import generate_athlete_bio
from app.services.athlete_profile_service import compute_profile_hash, get_profile_schema

# Fields that trigger bio regeneration
TRIGGER_FIELDS = {
    "identity.first_name",
    "identity.age",
    "identity.location",
    "goals.primary_goal",
    "goals.goal_type",
    "goals.target_event",
    "goals.target_date",
    "training_context.primary_sport",
    "training_context.experience_level",
    "training_context.years_training",
    "training_context.current_phase",
    "constraints.availability_days_per_week",
    "constraints.availability_hours_per_week",
    "constraints.injury_status",
    "preferences.recovery_preference",
    "preferences.coaching_style",
}


def handle_profile_change(
    session: Session,
    user_id: str,
    changed_fields: list[str],
) -> None:
    """Handle profile change and trigger bio regeneration if needed.

    This function:
    1. Checks if any changed fields are in TRIGGER_FIELDS
    2. Gets the current bio
    3. If bio.source == 'ai_generated' → regenerate
    4. If bio.source != 'ai_generated' → mark stale

    Args:
        session: Database session
        user_id: User ID
        changed_fields: List of changed field paths (e.g., ['identity.first_name', 'goals.primary_goal'])
    """
    # Check if any trigger fields changed
    has_trigger_fields = any(field in TRIGGER_FIELDS for field in changed_fields)

    if not has_trigger_fields:
        logger.debug("No trigger fields changed, skipping bio regeneration", user_id=user_id, changed_fields=changed_fields)
        return

    logger.info("Trigger fields changed, checking bio regeneration", user_id=user_id, changed_fields=changed_fields)

    # Get current bio
    bio = session.query(AthleteBio).filter_by(user_id=user_id).order_by(AthleteBio.created_at.desc()).first()

    if not bio:
        # No bio exists - generate one
        logger.info("No bio exists, generating new bio", user_id=user_id)
        regenerate_bio(session, user_id)
        return

    # Check bio source
    if bio.source == "ai_generated":
        # Regenerate automatically
        logger.info("Bio is AI-generated, regenerating", user_id=user_id, bio_id=bio.id)
        regenerate_bio(session, user_id)
    else:
        # Mark as stale (user has edited it manually)
        logger.info("Bio is user-edited, marking as stale", user_id=user_id, bio_id=bio.id)
        bio.stale = True
        session.flush()


def regenerate_bio(session: Session, user_id: str) -> None:
    """Regenerate bio for user.

    This function:
    1. Gets the current profile
    2. Generates new bio
    3. Computes profile hash
    4. Creates or updates bio record

    Args:
        session: Database session
        user_id: User ID
    """
    # Get profile
    profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
    if not profile:
        logger.warning("Profile not found, skipping bio generation", user_id=user_id)
        return

    # Get profile schema
    profile_schema = get_profile_schema(session, user_id)

    # Generate bio (async)
    try:
        # Run async function in sync context
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, we need to use a different approach
            # For now, use asyncio.run in a thread or create a task
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, generate_athlete_bio(profile_schema))
                bio_result = future.result()
        else:
            bio_result = loop.run_until_complete(generate_athlete_bio(profile_schema))
    except RuntimeError:
        # No event loop, create new one
        bio_result = asyncio.run(generate_athlete_bio(profile_schema))

    # Compute profile hash
    profile_hash = compute_profile_hash(profile)

    # Get or create bio record
    bio = session.query(AthleteBio).filter_by(user_id=user_id).order_by(AthleteBio.created_at.desc()).first()

    if bio and bio.source == "ai_generated":
        # Update existing AI-generated bio
        bio.text = bio_result.text
        bio.confidence_score = bio_result.confidence_score
        bio.depends_on_hash = profile_hash
        bio.last_generated_at = datetime.now(timezone.utc)
        bio.stale = False
        logger.info("Updated existing AI-generated bio", user_id=user_id, bio_id=bio.id)
    else:
        # Create new bio record
        new_bio = AthleteBio(
            id=str(uuid4()),
            user_id=user_id,
            text=bio_result.text,
            confidence_score=bio_result.confidence_score,
            source="ai_generated",
            depends_on_hash=profile_hash,
            last_generated_at=datetime.now(timezone.utc),
            stale=False,
        )
        session.add(new_bio)
        logger.info("Created new bio", user_id=user_id, bio_id=new_bio.id)

    session.flush()
