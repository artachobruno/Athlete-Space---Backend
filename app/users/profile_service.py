"""Service for managing athlete profiles from Strava and user input.

Handles non-destructive merging of Strava profile data into athlete profiles.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.athlete_profile import AthleteProfileUpsert
from app.db.models import AthleteProfile, StravaAccount, User, UserSettings


def create_user_settings(user_id: str) -> UserSettings:
    """Create a new UserSettings instance with default preferences.

    Schema v2: All settings are stored in the preferences JSONB column.

    Args:
        user_id: User ID (primary key)

    Returns:
        UserSettings instance with default preferences
    """
    default_preferences = {
        "units": "metric",
        "timezone": "UTC",
        "notifications_enabled": True,
        "email_notifications": False,
        "weekly_summary": True,
        "profile_visibility": "private",
        "share_activity_data": False,
        "share_training_metrics": False,
        "push_notifications": True,
        "workout_reminders": True,
        "training_load_alerts": True,
        "race_reminders": True,
        "goal_achievements": True,
        "coach_messages": True,
    }
    return UserSettings(
        user_id=user_id,
        preferences=default_preferences,
    )


def validate_user_settings_not_null(settings: UserSettings) -> None:
    """Validate that UserSettings has valid preferences.

    Schema v2: All settings are stored in the preferences JSONB column.

    Args:
        settings: UserSettings instance to validate

    Raises:
        ValueError: If preferences is None
    """
    if settings.preferences is None:
        raise ValueError("preferences must not be None")


def map_gender(strava_sex: str | None) -> str | None:
    """Map Strava sex field to internal gender field.

    Args:
        strava_sex: Strava sex field ("M", "F", or None)

    Returns:
        Gender string ("M", "F", or None)
    """
    if strava_sex in {"M", "F"}:
        return strava_sex
    return None


def format_location(city: str | None, state: str | None, country: str | None) -> str | None:
    """Format location from Strava fields into a single string.

    Args:
        city: City name (optional)
        state: State/province name (optional)
        country: Country name (optional)

    Returns:
        Formatted location string (e.g., "Brentwood, TN, US") or None if all fields are empty
    """
    parts = []
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    if country:
        parts.append(country)

    if not parts:
        return None

    return ", ".join(parts)


def _merge_name_field(profile: AthleteProfile, firstname: str | None, lastname: str | None) -> None:
    """Merge name field from Strava data."""
    if profile.sources is None:
        profile.sources = {}
    if not profile.name and firstname and lastname:
        profile.name = f"{firstname} {lastname}"
        profile.sources["name"] = "strava"
        logger.info(f"[PROFILE_SERVICE] Set name from Strava: {profile.name}")
    elif firstname and lastname and profile.sources.get("name") == "strava":
        profile.name = f"{firstname} {lastname}"
        logger.info(f"[PROFILE_SERVICE] Updated name from Strava: {profile.name}")


def _merge_gender_field(profile: AthleteProfile, sex: str | None) -> None:
    """Merge gender field from Strava data."""
    if profile.sources is None:
        profile.sources = {}
    if profile.gender is None:
        gender = map_gender(sex)
        if gender:
            profile.gender = gender
            profile.sources["gender"] = "strava"
            logger.info(f"[PROFILE_SERVICE] Set gender from Strava: {profile.gender}")
    elif profile.sources.get("gender") == "strava":
        gender = map_gender(sex)
        if gender:
            profile.gender = gender
            logger.info(f"[PROFILE_SERVICE] Updated gender from Strava: {profile.gender}")


def _merge_weight_field(profile: AthleteProfile, weight: float | None) -> None:
    """Merge weight field from Strava data."""
    if profile.sources is None:
        profile.sources = {}
    if profile.weight_kg is None and weight is not None:
        profile.weight_kg = float(weight)
        profile.sources["weight_kg"] = "strava"
        logger.info(f"[PROFILE_SERVICE] Set weight from Strava: {profile.weight_kg} kg")
    elif weight is not None and profile.sources.get("weight_kg") == "strava":
        profile.weight_kg = float(weight)
        logger.info(f"[PROFILE_SERVICE] Updated weight from Strava: {profile.weight_kg} kg")


def _merge_location_field(
    profile: AthleteProfile,
    city: str | None,
    state: str | None,
    country: str | None,
) -> None:
    """Merge location field from Strava data."""
    if profile.sources is None:
        profile.sources = {}
    if not profile.location:
        location = format_location(city, state, country)
        if location:
            profile.location = location
            profile.sources["location"] = "strava"
            logger.info(f"[PROFILE_SERVICE] Set location from Strava: {profile.location}")
    elif profile.sources.get("location") == "strava":
        location = format_location(city, state, country)
        if location:
            profile.location = location
            logger.info(f"[PROFILE_SERVICE] Updated location from Strava: {profile.location}")


def merge_strava_profile(
    session: Session,
    user_id: str,
    strava_athlete: dict,
) -> AthleteProfile:
    """Merge Strava athlete profile data into AthleteProfile (non-destructive).

    Only updates fields that are currently null/empty.
    Never overwrites user-provided data.

    Args:
        session: Database session
        user_id: User ID
        strava_athlete: Strava athlete API response dictionary

    Returns:
        Updated AthleteProfile instance
    """
    logger.info(f"[PROFILE_SERVICE] Merging Strava profile for user_id={user_id}")

    # Get or create profile
    profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
    if not profile:
        logger.info(f"[PROFILE_SERVICE] Creating new profile for user_id={user_id}")
        # Get athlete_id from strava_athlete if available
        athlete_id = 0
        athlete_id_from_strava = strava_athlete.get("id")
        if athlete_id_from_strava:
            try:
                athlete_id = int(athlete_id_from_strava)
            except (ValueError, TypeError):
                athlete_id = 0
        profile = AthleteProfile(
            user_id=user_id,
            athlete_id=athlete_id,
            sources={},
        )
        session.add(profile)

    # Ensure sources dict is initialized
    if profile.sources is None:
        profile.sources = {}

    # Extract Strava fields (only allowed fields)
    firstname = strava_athlete.get("firstname")
    lastname = strava_athlete.get("lastname")
    sex = strava_athlete.get("sex")
    weight = strava_athlete.get("weight")
    city = strava_athlete.get("city")
    state = strava_athlete.get("state")
    country = strava_athlete.get("country")
    athlete_id = strava_athlete.get("id")

    # Merge profile fields
    _merge_name_field(profile, firstname, lastname)
    _merge_gender_field(profile, sex)
    _merge_weight_field(profile, weight)
    _merge_location_field(profile, city, state, country)

    # Set Strava connection info
    if athlete_id:
        profile.strava_athlete_id = int(athlete_id)
    profile.strava_connected = True

    # Ensure onboarding is not marked complete (user must still complete it)
    profile.onboarding_completed = False

    session.commit()
    logger.info(f"[PROFILE_SERVICE] Profile merged successfully for user_id={user_id}")

    return profile


def upsert_athlete_profile(
    *,
    user_id: str,
    payload: AthleteProfileUpsert,
    session: Session,
) -> tuple[User, AthleteProfile, UserSettings]:
    """Upsert athlete profile data to users, athlete_profiles, and user_settings.

    This is the single source of truth for profile updates.
    Used by both onboarding completion and settings update endpoints.

    This function:
    1. Updates users table (first_name, last_name, timezone)
    2. Upserts athlete_profiles row (primary_sport, onboarding_completed)
    3. Upserts user_settings row (training preferences, availability, injury info)
    4. Marks onboarding_completed = True (only if not already)

    Args:
        user_id: User ID
        payload: Profile data to upsert
        session: Database session

    Returns:
        Tuple of (User, AthleteProfile, UserSettings) instances

    Raises:
        ValueError: If user not found
    """
    logger.info(f"[PROFILE_SERVICE] Upserting athlete profile for user_id={user_id}")

    # Guard assertion: fail fast if user_id is missing or invalid
    if user_id is None:
        raise ValueError("user_id must be provided and cannot be None")
    if not isinstance(user_id, str):
        raise TypeError(f"user_id must be a string, got {type(user_id).__name__}")
    if not user_id.strip():
        raise ValueError("user_id must be provided and cannot be empty")

    # 1. Update User table
    user_result = session.execute(select(User).where(User.id == user_id)).first()
    if not user_result:
        raise ValueError(f"User not found: {user_id}")
    user = user_result[0]

    user.first_name = payload.first_name
    user.last_name = payload.last_name
    user.timezone = payload.timezone
    # Mark onboarding as complete on User table (the authoritative flag)
    user.onboarding_complete = True

    # 2. Get or create AthleteProfile
    profile_result = session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
    if profile_result:
        profile = profile_result[0]
    else:
        # Create new profile with only valid columns (schema v2)
        profile = AthleteProfile(user_id=user_id)
        session.add(profile)

    # Update profile fields - copy name from User to AthleteProfile for convenience
    profile.first_name = payload.first_name
    profile.last_name = payload.last_name

    # 3. Get or create UserSettings
    settings_result = session.execute(select(UserSettings).where(UserSettings.user_id == user_id)).first()
    if settings_result:
        settings = settings_result[0]
    else:
        settings = create_user_settings(user_id=user_id)
        session.add(settings)

    # Update preferences JSONB field with all training settings
    # Initialize preferences dict if None
    if settings.preferences is None:
        settings.preferences = {}

    # Map goal_type to training_focus
    goal_type_to_focus = {
        "performance": "race_focused",
        "completion": "race_focused",
        "general": "general_fitness",
    }

    # Convert availability_days_per_week (int) to available_days (list of day names)
    week_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    available_days = week_days[: payload.availability_days_per_week]

    # Build updated preferences dict
    updated_preferences = {
        **settings.preferences,
        "primary_sports": [payload.primary_sport],
        "training_focus": goal_type_to_focus.get(payload.goal_type, "general_fitness"),
        "consistency": payload.experience_level,
        "available_days": available_days,
        "weekly_hours": payload.availability_hours_per_week,
        "injury_history": payload.injury_status != "none",
        "injury_notes": payload.injury_notes if payload.injury_notes else None,
        "onboarding_completed": True,
    }

    # Assign the new dict to trigger SQLAlchemy change detection
    settings.preferences = updated_preferences

    # Validate all NOT NULL constraints before commit (hard guardrail)
    validate_user_settings_not_null(settings)

    # Commit all changes atomically
    try:
        session.commit()
    except Exception as e:
        logger.exception(
            f"[PROFILE_SERVICE] Failed to commit profile upsert for user_id={user_id}: {e}"
        )
        # Re-raise with more context if it's a KeyError
        if isinstance(e, KeyError):
            missing_key = str(e.args[0]) if e.args else "unknown"
            # Strip quotes if present (handles both "'user_id'" and "user_id")
            if (missing_key.startswith("'") and missing_key.endswith("'")) or (missing_key.startswith('"') and missing_key.endswith('"')):
                missing_key = missing_key[1:-1]
            raise KeyError(missing_key) from e
        raise

    logger.info(
        f"[PROFILE_SERVICE] Profile upserted successfully for user_id={user_id}: "
        f"first_name={payload.first_name}, primary_sport={payload.primary_sport}, "
        f"experience_level={payload.experience_level}"
    )

    return (user, profile, settings)
