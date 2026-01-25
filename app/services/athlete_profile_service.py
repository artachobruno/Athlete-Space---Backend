"""Athlete profile service for CRUD operations and merge logic.

This service handles all athlete profile operations including:
- Getting or creating profiles
- Partial updates with merge logic
- Profile hash computation for bio regeneration triggers
"""

import hashlib
import json
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.db.models import AthleteBio, AthleteProfile
from app.models.athlete_profile import (
    AthleteProfile as AthleteProfileSchema,
)
from app.models.athlete_profile import (
    ConstraintProfile,
    GoalProfile,
    IdentityProfile,
    NarrativeBio,
    PreferenceProfile,
    TrainingContextProfile,
)


def get_or_create_profile(session: Session, user_id: str) -> AthleteProfile:
    """Get or create an athlete profile for the user.

    If the profile doesn't exist, creates an empty one with default values.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        AthleteProfile instance (always exists)
    """
    profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()

    if not profile:
        logger.info("Creating new athlete profile", user_id=user_id)
        profile = AthleteProfile(user_id=user_id)
        session.add(profile)
        session.flush()

    return profile


def update_structured_profile(
    session: Session,
    user_id: str,
    partial_update: dict[str, Any],
) -> AthleteProfile:
    """Update structured profile with partial update.

    This function performs a deep merge of the partial update into the existing profile.
    Only updates the fields provided in partial_update.

    Args:
        session: Database session
        user_id: User ID
        partial_update: Dictionary with partial profile updates
            Keys can be: identity, goals, constraints, training_context, preferences
            Values should be dictionaries or None (to clear a section)

    Returns:
        Updated AthleteProfile instance (always full profile object)

    Example:
        >>> update_structured_profile(session, user_id, {
        ...     "identity": {"first_name": "John"},
        ...     "goals": {"primary_goal": "Marathon PR"}
        ... })
    """
    profile = get_or_create_profile(session, user_id)

    # Deep merge for each section
    sections = ["identity", "goals", "constraints", "training_context", "preferences"]

    for section in sections:
        if section not in partial_update:
            continue

        new_data = partial_update[section]

        # If None, clear the section
        if new_data is None:
            setattr(profile, section, None)
            continue

        # Get existing data or empty dict
        existing_data = getattr(profile, section) or {}

        # Deep merge
        merged_data = _deep_merge(existing_data, new_data)
        setattr(profile, section, merged_data)

    session.flush()
    logger.info("Updated structured profile", user_id=user_id, updated_sections=list(partial_update.keys()))

    return profile


def compute_profile_hash(profile: AthleteProfile) -> str:
    """Compute hash of profile data (excluding narrative_bio).

    This hash is used to determine if the bio needs regeneration.
    The hash includes all structured profile fields but excludes the bio.

    Args:
        profile: AthleteProfile instance

    Returns:
        SHA256 hash of profile data (hex string)
    """
    # Collect all structured fields (exclude narrative_bio)
    profile_data = {
        "identity": profile.identity or {},
        "goals": profile.goals or {},
        "constraints": profile.constraints or {},
        "training_context": profile.training_context or {},
        "preferences": profile.preferences or {},
    }

    # Sort keys for consistent hashing
    sorted_data = json.dumps(profile_data, sort_keys=True, default=str)

    # Compute hash
    hash_obj = hashlib.sha256(sorted_data.encode("utf-8"))
    return hash_obj.hexdigest()


def get_profile_schema(session: Session, user_id: str) -> AthleteProfileSchema:
    """Get profile as Pydantic schema.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        AthleteProfileSchema instance with all profile data
    """
    profile = get_or_create_profile(session, user_id)

    # Convert JSONB fields to Pydantic models
    identity = IdentityProfile.model_validate(profile.identity or {})
    goals = GoalProfile.model_validate(profile.goals or {})
    constraints = ConstraintProfile.model_validate(profile.constraints or {})
    training_context = TrainingContextProfile.model_validate(profile.training_context or {})
    preferences = PreferenceProfile.model_validate(profile.preferences or {})

    # Get bio if exists (handle case where table doesn't exist yet)
    bio = None
    try:
        bio_record = session.query(AthleteBio).filter_by(user_id=user_id).order_by(AthleteBio.created_at.desc()).first()
        if bio_record:
            bio = NarrativeBio(
                text=bio_record.text,
                confidence_score=bio_record.confidence_score,
                source=bio_record.source,
                depends_on_hash=bio_record.depends_on_hash,
            )
    except ProgrammingError as e:
        # Table doesn't exist yet (migration not run)
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedtable" in error_msg or "no such table" in error_msg:
            logger.warning(
                "athlete_bios table does not exist yet for user_id=%s, skipping bio retrieval. "
                "Run migrations to create the table.",
                user_id,
            )
        else:
            # Re-raise if it's a different programming error
            raise

    return AthleteProfileSchema(
        identity=identity,
        goals=goals,
        constraints=constraints,
        training_context=training_context,
        preferences=preferences,
        narrative_bio=bio,
    )


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries.

    Args:
        base: Base dictionary
        update: Update dictionary

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result
