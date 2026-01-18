"""Read-only access to athlete profile.

Single source of personalization truth.
"""

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AthleteProfile, StravaAccount, UserSettings
from app.db.session import get_session
from app.tools.interfaces import AthleteProfile as AthleteProfileInterface


def _get_athlete_id_from_user_id(session: Session, user_id: str) -> str:
    """Get athlete_id from user_id via AthleteProfile or StravaAccount.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        Athlete ID as string, or user_id as fallback
    """
    # Try AthleteProfile first
    profile_result = session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
    if profile_result and profile_result[0].athlete_id:
        return str(profile_result[0].athlete_id)

    # Fallback to StravaAccount
    account_result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
    if account_result:
        return str(account_result[0].athlete_id)

    # Final fallback to user_id
    return user_id


def _calculate_age(birthdate: date | None) -> int | None:
    """Calculate age from birthdate.

    Args:
        birthdate: Birth date

    Returns:
        Age in years, or None if birthdate is not available
    """
    if birthdate is None:
        return None

    today = datetime.now(timezone.utc).date()
    age = today.year - birthdate.year
    # Adjust if birthday hasn't occurred this year
    if (today.month, today.day) < (birthdate.month, birthdate.day):
        age -= 1
    return age


def get_athlete_profile(user_id: str) -> AthleteProfileInterface:
    """Get athlete profile information.

    READ-ONLY: Single source of personalization truth.
    Never returns None - missing fields are allowed.

    Args:
        user_id: User ID

    Returns:
        AthleteProfile with available data (missing fields are None)
    """
    logger.debug(f"Reading athlete profile: user_id={user_id}")

    with get_session() as session:
        # Get athlete_id
        athlete_id = _get_athlete_id_from_user_id(session, user_id)

        # Get AthleteProfile
        profile_result = session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
        profile = profile_result[0] if profile_result else None

        # Get UserSettings for preferences
        settings_result = session.execute(select(UserSettings).where(UserSettings.user_id == user_id)).first()
        settings = settings_result[0] if settings_result else None

        # Extract fields
        birthdate = profile.birthdate if profile else None
        age = _calculate_age(birthdate)
        sex = profile.sex if profile else None

        # Training age years - not directly stored, would require computation from first activity
        # For Phase 1, return None
        training_age_years = None

        # Preferred rest days - not directly stored in current schema
        # Would need to be inferred from training patterns or user preferences
        preferred_rest_days = None

        # Max weekly hours - not directly stored, would need to be inferred from preferences
        # Check UserSettings.preferences for availability_hours_per_week
        max_weekly_hours = None
        if settings and settings.preferences:
            availability_hours = settings.preferences.get("availability_hours_per_week")
            if availability_hours is not None:
                max_weekly_hours = float(availability_hours)

        return AthleteProfileInterface(
            athlete_id=athlete_id,
            age=age,
            sex=sex,
            training_age_years=training_age_years,
            preferred_rest_days=preferred_rest_days,
            max_weekly_hours=max_weekly_hours,
        )
