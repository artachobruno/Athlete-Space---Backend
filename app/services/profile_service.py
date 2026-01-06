"""Service for managing athlete profiles from Strava and user input.

Handles non-destructive merging of Strava profile data into athlete profiles.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.orm import Session

from app.db.models import AthleteProfile


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
    if not profile.name and firstname and lastname:
        profile.name = f"{firstname} {lastname}"
        profile.sources["name"] = "strava"
        logger.info(f"[PROFILE_SERVICE] Set name from Strava: {profile.name}")
    elif firstname and lastname and profile.sources.get("name") == "strava":
        profile.name = f"{firstname} {lastname}"
        logger.info(f"[PROFILE_SERVICE] Updated name from Strava: {profile.name}")


def _merge_gender_field(profile: AthleteProfile, sex: str | None) -> None:
    """Merge gender field from Strava data."""
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
        profile = AthleteProfile(
            user_id=user_id,
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
