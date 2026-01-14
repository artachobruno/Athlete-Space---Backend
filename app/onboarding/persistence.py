"""Persistence logic for profile and training preferences during onboarding."""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.athlete_profile import AthleteProfileUpsert
from app.api.schemas.schemas import AthleteProfileUpdateRequest, TrainingPreferencesUpdateRequest
from app.db.models import AthleteProfile, StravaAccount, User, UserRole, UserSettings
from app.onboarding.schemas import OnboardingCompleteRequest
from app.services.training_preferences import extract_and_store_race_info
from app.users.profile_service import upsert_athlete_profile


def persist_profile_data(
    session: Session,
    user_id: str,
    profile_data: AthleteProfileUpdateRequest | None,
) -> AthleteProfile:
    """Persist profile data.

    Args:
        session: Database session
        user_id: User ID
        profile_data: Profile data to persist

    Returns:
        AthleteProfile instance
    """
    if not profile_data:
        # Return existing profile or create empty one
        profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
        if not profile:
            strava_account = session.query(StravaAccount).filter_by(user_id=user_id).first()
            athlete_id = int(strava_account.athlete_id) if strava_account else 0
            profile = AthleteProfile(user_id=user_id, athlete_id=athlete_id, sources={})
            session.add(profile)
        return profile

    profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
    if not profile:
        strava_account = session.query(StravaAccount).filter_by(user_id=user_id).first()
        athlete_id = int(strava_account.athlete_id) if strava_account else 0
        profile = AthleteProfile(user_id=user_id, athlete_id=athlete_id, sources={})
        session.add(profile)

    # Update fields
    if profile.sources is None:
        profile.sources = {}

    if profile_data.name is not None:
        profile.name = profile_data.name
        profile.sources["name"] = "user"

    if profile_data.email is not None:
        profile.email = profile_data.email

    if profile_data.gender is not None:
        profile.gender = profile_data.gender
        profile.sources["gender"] = "user"

    if profile_data.date_of_birth is not None:
        try:
            parsed_date = datetime.strptime(profile_data.date_of_birth, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            profile.date_of_birth = parsed_date
        except ValueError:
            pass  # Skip invalid dates

    if profile_data.weight_kg is not None:
        profile.weight_kg = profile_data.weight_kg
        profile.sources["weight_kg"] = "user"

    if profile_data.height_cm is not None:
        profile.height_cm = profile_data.height_cm
        profile.sources["height_cm"] = "user"

    if profile_data.weight_lbs is not None:
        profile.weight_lbs = round(float(profile_data.weight_lbs), 1)
        profile.sources["weight_lbs"] = "user"

    if profile_data.height_in is not None:
        profile.height_in = round(float(profile_data.height_in), 1)
        profile.sources["height_in"] = "user"

    if profile_data.location is not None:
        profile.location = profile_data.location
        profile.sources["location"] = "user"

    if profile_data.unit_system is not None:
        profile.unit_system = profile_data.unit_system

    if profile_data.target_event is not None:
        profile.target_event = {
            "name": profile_data.target_event.name,
            "date": profile_data.target_event.date,
            "distance": profile_data.target_event.distance,
        }

    if profile_data.goals is not None:
        profile.goals = profile_data.goals

    session.commit()
    return profile


def persist_training_preferences(
    session: Session,
    user_id: str,
    preferences_data: TrainingPreferencesUpdateRequest | None,
) -> UserSettings:
    """Persist training preferences.

    Args:
        session: Database session
        user_id: User ID
        preferences_data: Training preferences to persist

    Returns:
        UserSettings instance
    """
    if not preferences_data:
        settings = session.query(UserSettings).filter_by(user_id=user_id).first()
        if not settings:
            settings = UserSettings(user_id=user_id)
            session.add(settings)
        return settings

    settings = session.query(UserSettings).filter_by(user_id=user_id).first()
    if not settings:
        settings = UserSettings(user_id=user_id)
        session.add(settings)

    # Update fields
    if preferences_data.years_of_training is not None:
        settings.years_of_training = preferences_data.years_of_training

    if preferences_data.primary_sports is not None:
        settings.primary_sports = preferences_data.primary_sports

    if preferences_data.available_days is not None:
        settings.available_days = preferences_data.available_days

    if preferences_data.weekly_hours is not None:
        settings.weekly_hours = preferences_data.weekly_hours

    if preferences_data.training_focus is not None:
        settings.training_focus = preferences_data.training_focus

    if preferences_data.injury_history is not None:
        settings.injury_history = preferences_data.injury_history

    if preferences_data.injury_notes is not None:
        settings.injury_notes = preferences_data.injury_notes

    if preferences_data.consistency is not None:
        settings.consistency = preferences_data.consistency

    if preferences_data.goal is not None:
        settings.goal = preferences_data.goal

    session.commit()

    # Trigger race extraction if goal field was set or changed
    if preferences_data.goal is not None:
        try:
            profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
            extract_and_store_race_info(session, user_id, settings, profile)
        except Exception as e:
            logger.error(f"Failed to extract race info during onboarding: {e}", exc_info=True)
            # Don't fail onboarding if extraction fails

    return settings


def persist_onboarding_complete(
    session: Session,
    user_id: str,
    request: OnboardingCompleteRequest,
) -> tuple[User, AthleteProfile, UserSettings]:
    """Persist complete onboarding data to users, athlete_profiles, and user_settings.

    This function uses the shared upsert_athlete_profile service to ensure
    consistency between onboarding and settings updates.

    Args:
        session: Database session
        user_id: User ID
        request: Onboarding completion request with all required fields

    Returns:
        Tuple of (User, AthleteProfile, UserSettings) instances
    """
    # Convert OnboardingCompleteRequest to AthleteProfileUpsert
    payload = AthleteProfileUpsert(
        first_name=request.first_name,
        last_name=request.last_name,
        timezone=request.timezone,
        primary_sport=request.primary_sport,
        goal_type=request.goal_type,
        experience_level=request.experience_level,
        availability_days_per_week=request.availability_days_per_week,
        availability_hours_per_week=request.availability_hours_per_week,
        injury_status=request.injury_status,
        injury_notes=request.injury_notes,
    )

    # Use shared service to upsert profile data
    user, profile, settings = upsert_athlete_profile(
        user_id=user_id,
        payload=payload,
        session=session,
    )

    # Handle role separately (only set during onboarding, not in settings)
    # Convert string to UserRole enum
    if request.role == "athlete":
        user.role = UserRole.athlete
    elif request.role == "coach":
        user.role = UserRole.coach
    else:
        raise ValueError(f"Invalid role: {request.role}. Must be 'athlete' or 'coach'")
    # Note: No need to commit here - upsert_athlete_profile already committed.
    # The role change will be committed by the session context manager.

    logger.info(
        f"Persisted onboarding data for user_id={user_id}: "
        f"first_name={request.first_name}, primary_sport={request.primary_sport}, "
        f"experience_level={request.experience_level}, role={request.role}"
    )

    return (user, profile, settings)
