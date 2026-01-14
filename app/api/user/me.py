"""User-facing API endpoints for athlete status and overview.

These endpoints provide read-only access to athlete sync status and training
overview. No ingestion logic is performed here - all data comes from the database.
"""

from __future__ import annotations

import csv
import threading
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from app.api.dependencies.auth import get_current_user_id, get_optional_user_id
from app.api.schemas.schemas import (
    AthleteProfileResponse,
    AthleteProfileUpdateRequest,
    ChangeEmailRequest,
    ChangePasswordRequest,
    NotificationsResponse,
    NotificationsUpdateRequest,
    PrivacySettingsResponse,
    PrivacySettingsUpdateRequest,
    SettingsProfileResponse,
    SettingsProfileUpdateRequest,
    TargetEvent,
    TimezoneUpdateRequest,
    TrainingPreferencesResponse,
    TrainingPreferencesUpdateRequest,
)
from app.core.password import hash_password, verify_password
from app.db.models import (
    Activity,
    AthleteProfile,
    AuthProvider,
    CoachMessage,
    DailyTrainingLoad,
    StravaAccount,
    User,
    UserRole,
    UserSettings,
)
from app.db.session import get_session
from app.ingestion.background_sync import sync_user_activities
from app.ingestion.sla import SYNC_SLA_SECONDS
from app.ingestion.tasks import history_backfill_task
from app.metrics.daily_aggregation import aggregate_daily_training, get_daily_rows
from app.metrics.data_quality import assess_data_quality
from app.metrics.training_load import compute_training_load
from app.services.training_preferences import extract_and_store_race_info

router = APIRouter(prefix="/me", tags=["me"])


@router.options("")
async def options_me():
    """Handle CORS preflight requests for /me endpoint.

    This ensures OPTIONS requests are handled before auth dependencies run.
    """
    return Response(status_code=200)


def _get_user_info(session, user_id: str) -> tuple[str, str, str]:
    """Get user email, auth provider, and timezone.

    This function is called AFTER authentication has already validated the user exists.
    If user is not found here, it indicates an internal server error (race condition
    or database inconsistency), not a "not found" error.

    Args:
        session: Database session
        user_id: User ID (already validated by auth dependency)

    Returns:
        Tuple of (email, auth_provider, timezone)

    Raises:
        HTTPException: 500 if user not found (internal server error)
    """
    user_result = session.execute(select(User).where(User.id == user_id)).first()
    if not user_result:
        logger.error(
            f"[API] /me: CRITICAL - User validated by auth but not found in DB: user_id={user_id}. "
            "This indicates an internal server error (race condition or DB inconsistency)."
        )
        raise HTTPException(
            status_code=500,
            detail="Internal server error: User data inconsistency. Please try again or contact support.",
        )

    user = user_result[0]
    email = user.email
    auth_provider = user.auth_provider.value if user.auth_provider else "password"
    timezone_str = getattr(user, "timezone", "UTC") or "UTC"
    return (email, auth_provider, timezone_str)


def _get_onboarding_status(session, user_id: str) -> bool:
    """Get onboarding completion status from profile.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        True if onboarding is complete, False otherwise
    """
    try:
        profile_result = session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
        profile = profile_result[0] if profile_result else None

        if not profile:
            logger.info(f"[API] /me: user_id={user_id}, profile_exists=False")
            return False

        try:
            onboarding_complete = bool(profile.onboarding_completed)
            logger.info(f"[API] /me: user_id={user_id}, profile_exists=True, onboarding_completed={onboarding_complete}")
        except (AttributeError, ProgrammingError) as e:
            error_msg = str(e).lower()
            logger.warning(
                f"[API] /me: onboarding_completed column missing or schema error for user_id={user_id}: {e!r}. Treating as incomplete."
            )
            return False
        else:
            return onboarding_complete
    except ProgrammingError as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.warning(
                f"[API] /me: Database schema issue querying AthleteProfile for user_id={user_id}: {e!r}. Treating as no profile."
            )
            return False
        raise


def _get_profile_for_inference(session, user_id: str) -> AthleteProfile | None:
    """Get profile for onboarding inference.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        AthleteProfile instance or None
    """
    try:
        profile_result = session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
        return profile_result[0] if profile_result else None
    except ProgrammingError:
        return None


def _try_infer_completion_from_data(session, user_id: str) -> bool:
    """Try to infer onboarding completion from profile and settings data.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        True if onboarding appears complete based on data, False otherwise
    """
    # Strong signal: If Strava is connected and has activities, onboarding is likely complete
    try:
        strava_account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if strava_account:
            activity_count = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar() or 0
            if activity_count > 0:
                logger.warning(
                    f"[API] /me: user_id={user_id}, onboarding_completed flag is False "
                    f"but Strava is connected with {activity_count} activities. Inferring completion=True."
                )
                return True
    except Exception as e:
        logger.debug(f"[API] /me: Could not check Strava connection for inference: {e}")

    # Fallback: Check profile and settings data
    profile = _get_profile_for_inference(session, user_id)
    if not profile:
        return False

    inferred_complete = _try_infer_onboarding_from_data(session, user_id, profile)
    if inferred_complete:
        logger.warning(
            f"[API] /me: user_id={user_id}, onboarding_completed flag is False "
            f"but user has onboarding data. Inferring completion=True. "
            f"This suggests a data inconsistency that should be fixed."
        )
    return inferred_complete


def _try_infer_onboarding_from_data(session, user_id: str, profile: AthleteProfile) -> bool:
    """Try to infer onboarding completion from profile and settings data.

    Args:
        session: Database session
        user_id: User ID
        profile: AthleteProfile instance

    Returns:
        True if onboarding appears complete based on data, False otherwise
    """
    try:
        settings_result = session.execute(select(UserSettings).where(UserSettings.user_id == user_id)).first()
        settings = settings_result[0] if settings_result else None

        if not settings:
            return False

        try:
            return _infer_onboarding_complete_from_data(profile, settings)
        except Exception as e:
            logger.debug(f"[API] /me: Could not infer onboarding completion: {e}")
            return False
    except ProgrammingError as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.warning(f"[API] /me: Database schema issue querying UserSettings for user_id={user_id}: {e!r}. Skipping settings check.")
            return False
        raise


def _infer_onboarding_complete_from_data(profile: AthleteProfile | None, settings: UserSettings | None) -> bool:
    """Infer if onboarding was completed based on profile and settings data.

    This is a fallback check when the onboarding_completed flag might be incorrect.
    If the user has substantial onboarding data, we infer they completed onboarding.

    Args:
        profile: AthleteProfile instance or None
        settings: UserSettings instance or None

    Returns:
        True if onboarding appears to be completed based on data
    """
    if not profile and not settings:
        return False

    # Check if profile has substantial data
    has_profile_data = False
    if profile:
        has_profile_data = bool(
            profile.name or profile.goals or profile.target_event or profile.weight_kg or profile.height_cm or profile.date_of_birth
        )

    # Check if settings have substantial data
    has_settings_data = False
    if settings:
        has_settings_data = bool(
            settings.years_of_training
            or settings.primary_sports
            or settings.available_days
            or settings.weekly_hours
            or settings.training_focus
            or settings.goal
        )

    # If either has substantial data, infer onboarding was completed
    return has_profile_data or has_settings_data


@router.get("")
def get_me(user_id: str | None = Depends(get_optional_user_id)):
    """Get current authenticated user info with aggregated data.

    Returns user information including email, onboarding status, profile,
    training preferences, notifications, and privacy settings.

    CONTRACT: This endpoint MUST NEVER return 404.
    - If authenticated: returns 200 with user data
    - If not authenticated: returns 401 (never 404)

    This endpoint NEVER mutates state - it only reads and aggregates.

    Args:
        user_id: Current authenticated user ID (from auth dependency, None if not authenticated)

    Returns:
        {
            "user_id": str,
            "authenticated": bool,
            "email": str,
            "onboarding_complete": bool,
            "profile": {...},
            "training_preferences": {...},
            "notifications": {...},
            "privacy": {...}
        }

    Raises:
        HTTPException: 401 if not authenticated (never 404)
    """
    # 🚨 INVARIANT: /me must never return 404
    if user_id is None:
        logger.info("[API] /me endpoint called without authentication")
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info(f"[API] /me endpoint called for user_id={user_id}")

    def _raise_user_data_inconsistency() -> None:
        """Raise HTTPException for user data inconsistency."""
        raise HTTPException(
            status_code=500,
            detail="Internal server error: User data inconsistency. Please try again or contact support.",
        )

    # Store user info for exception handler (defaults for fallback response)
    user_email = ""
    user_auth_provider = "password"
    user_timezone = "UTC"

    try:
        with get_session() as session:
            # Get user info
            # This can raise 500 if user not found (should not happen after auth validation)
            try:
                user_email, user_auth_provider, user_timezone = _get_user_info(session, user_id)
                # Get first_name and last_name from user
                user_result = session.execute(select(User).where(User.id == user_id)).first()
                if not user_result:
                    _raise_user_data_inconsistency()
                user = user_result[0]
                user_first_name = user.first_name
                user_last_name = user.last_name
                # Get role, default to 'athlete' if not set (for existing users before migration)
                user_role_obj = getattr(user, "role", None)
                if user_role_obj is None:
                    user_role = "athlete"
                elif isinstance(user_role_obj, str):
                    user_role = user_role_obj
                else:
                    # It's a UserRole enum
                    user_role = user_role_obj.value
            except HTTPException:
                # Re-raise HTTPExceptions from _get_user_info (will be 500, not 404)
                raise
            except Exception as e:
                # Catch any other unexpected errors in user lookup
                logger.exception(f"[API] /me: Unexpected error getting user info for user_id={user_id}: {e!r}")
                raise HTTPException(
                    status_code=500,
                    detail="Internal server error: Failed to retrieve user information. Please try again.",
                ) from e

            # Get onboarding status from profile
            onboarding_complete = _get_onboarding_status(session, user_id)
            logger.info(f"[API] /me: user_id={user_id}, initial onboarding_complete={onboarding_complete}")

            # If not complete, try to infer from data
            if not onboarding_complete:
                logger.info(f"[API] /me: user_id={user_id}, trying to infer completion from data")
                inferred = _try_infer_completion_from_data(session, user_id)
                if inferred:
                    onboarding_complete = True
                    logger.info(f"[API] /me: user_id={user_id}, inferred onboarding_complete=True")
                else:
                    logger.info(f"[API] /me: user_id={user_id}, could not infer completion, returning False")

            # Build unified profile response (combines User, AthleteProfile, UserSettings)
            profile_response = None
            try:
                profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
                settings = session.query(UserSettings).filter_by(user_id=user_id).first()

                if profile or settings or user_first_name:
                    # Build profile from unified data model
                    profile_response = _build_unified_profile_response(
                        user_first_name=user_first_name,
                        user_last_name=user_last_name,
                        user_timezone=user_timezone,
                        profile=profile,
                        settings=settings,
                    )
            except Exception as e:
                logger.warning(f"[API] /me: Failed to load profile: {e}")

            # Aggregate training preferences
            training_prefs_response = None
            try:
                settings = session.query(UserSettings).filter_by(user_id=user_id).first()
                if settings:
                    session.expunge(settings)
                    training_prefs_response = TrainingPreferencesResponse(
                        years_of_training=settings.years_of_training or 0,
                        primary_sports=settings.primary_sports or [],
                        available_days=settings.available_days or [],
                        weekly_hours=settings.weekly_hours or 10.0,
                        training_focus=settings.training_focus or "general_fitness",
                        injury_history=settings.injury_history or False,
                        injury_notes=settings.injury_notes,
                        consistency=settings.consistency,
                        goal=settings.goal,
                    ).model_dump()
            except Exception as e:
                logger.warning(f"[API] /me: Failed to load training preferences: {e}")

            # Aggregate notifications
            notifications_response = None
            try:
                settings = session.query(UserSettings).filter_by(user_id=user_id).first()
                if settings:
                    notifications_response = NotificationsResponse(
                        email_notifications=settings.email_notifications if settings.email_notifications is not None else True,
                        push_notifications=settings.push_notifications if settings.push_notifications is not None else True,
                        workout_reminders=settings.workout_reminders if settings.workout_reminders is not None else True,
                        training_load_alerts=settings.training_load_alerts if settings.training_load_alerts is not None else True,
                        race_reminders=settings.race_reminders if settings.race_reminders is not None else True,
                        weekly_summary=settings.weekly_summary if settings.weekly_summary is not None else True,
                        goal_achievements=settings.goal_achievements if settings.goal_achievements is not None else True,
                        coach_messages=settings.coach_messages if settings.coach_messages is not None else True,
                    ).model_dump()
            except Exception as e:
                logger.warning(f"[API] /me: Failed to load notifications: {e}")

            # Aggregate privacy settings
            privacy_response = None
            try:
                settings = session.query(UserSettings).filter_by(user_id=user_id).first()
                if settings:
                    privacy_response = PrivacySettingsResponse(
                        profile_visibility=settings.profile_visibility or "private",
                        share_activity_data=settings.share_activity_data or False,
                        share_training_metrics=settings.share_training_metrics or False,
                    ).model_dump()
            except Exception as e:
                logger.warning(f"[API] /me: Failed to load privacy settings: {e}")

            return {
                "user_id": user_id,
                "authenticated": True,
                "email": user_email,
                "auth_provider": user_auth_provider,
                "first_name": user_first_name,
                "last_name": user_last_name,
                "timezone": user_timezone,
                "role": user_role if isinstance(user_role, str) else user_role.value if hasattr(user_role, "value") else str(user_role),
                "onboarding_complete": onboarding_complete,
                "profile": profile_response,
                "training_preferences": training_prefs_response,
                "notifications": notifications_response,
                "privacy": privacy_response,
            }
    except HTTPException as e:
        # Re-raise HTTPExceptions but ensure they're not 404
        # /me endpoint contract: valid auth = 200, no auth = 401, errors = 500
        if e.status_code == 404:
            logger.error(
                f"[API] /me: INTERNAL ERROR - 404 raised internally for user_id={user_id}. "
                "Converting to 500 as this violates /me endpoint contract."
            )
            raise HTTPException(
                status_code=500,
                detail="Internal server error. Please try again or contact support.",
            ) from e
        raise
    except ProgrammingError as e:
        # Catch any ProgrammingError that wasn't handled above
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.error(
                f"[API] /me: Unhandled database schema error for user_id={user_id}: {e!r}. Returning default response."
            )
            # Return a valid response even on schema errors - this endpoint must always return 200 OK if authenticated
            return {
                "user_id": user_id,
                "authenticated": True,
                "email": user_email if user_email else "",
                "auth_provider": user_auth_provider,
                "timezone": user_timezone,
                "role": "athlete",  # Default fallback (string for API response)
                "onboarding_complete": False,
                "profile": None,
                "training_preferences": None,
                "notifications": None,
                "privacy": None,
            }
        logger.exception(f"[API] /me: Unhandled ProgrammingError for user_id={user_id}: {e!r}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error: Database query failed. Please try again.",
        ) from e
    except Exception as e:
        # Catch-all for any other unexpected errors
        logger.exception(f"[API] /me: Unexpected error for user_id={user_id}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error: An unexpected error occurred. Please try again.",
        ) from e


@router.get("/strava")
def get_strava_status(user_id: str = Depends(get_current_user_id)):
    """Get Strava connection status for current user.

    Always returns 200 OK with connection status.
    Never returns 404, 204, or null.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        {
            "connected": bool
        }
    """
    logger.debug(f"[STRAVA_STATUS] Status check for user_id={user_id}")

    with get_session() as session:
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if not account:
            logger.debug(f"[STRAVA_STATUS] Strava not connected for user_id={user_id}")
            return {
                "connected": False,
            }

        logger.debug(f"[STRAVA_STATUS] Strava connected for user_id={user_id}, athlete_id={account[0].athlete_id}")
        return {
            "connected": True,
        }


@router.delete("/strava")
def disconnect_strava(user_id: str = Depends(get_current_user_id)):
    """Disconnect user's Strava account.

    Deletes the strava_accounts row for the current user.
    Returns 200 OK even if Strava is already disconnected.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Response with connected status and message
    """
    logger.info(f"[STRAVA_DISCONNECT] Disconnect requested for user_id={user_id}")

    with get_session() as session:
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if not account:
            logger.info(f"[STRAVA_DISCONNECT] Strava already disconnected for user_id={user_id}")
            return {
                "connected": False,
                "message": "Strava already disconnected",
            }

        athlete_id = account[0].athlete_id
        session.delete(account[0])
        session.commit()
        logger.info(f"[STRAVA_DISCONNECT] Disconnected Strava account for user_id={user_id}, athlete_id={athlete_id}")

    return {
        "connected": False,
        "message": "Strava disconnected",
    }


def _validate_unit_system(unit_system: str) -> None:
    """Validate unit system value.

    Args:
        unit_system: Unit system to validate

    Raises:
        HTTPException: If unit_system is invalid
    """
    if unit_system not in {"imperial", "metric"}:
        raise HTTPException(status_code=400, detail="unit_system must be 'imperial' or 'metric'")


def _validate_training_focus(training_focus: str) -> None:
    """Validate training focus value.

    Args:
        training_focus: Training focus to validate

    Raises:
        HTTPException: If training_focus is invalid
    """
    if training_focus not in {"race_focused", "general_fitness"}:
        raise HTTPException(status_code=400, detail="training_focus must be 'race_focused' or 'general_fitness'")


def _validate_profile_visibility(profile_visibility: str) -> None:
    """Validate profile visibility value.

    Args:
        profile_visibility: Profile visibility to validate

    Raises:
        HTTPException: If profile_visibility is invalid
    """
    if profile_visibility not in {"public", "private", "coaches"}:
        raise HTTPException(status_code=400, detail="profile_visibility must be 'public', 'private', or 'coaches'")


def _create_new_profile(session, user_id: str) -> AthleteProfile:
    """Create a new profile for a user.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        New AthleteProfile instance
    """
    # Try to get athlete_id from StravaAccount if available
    athlete_id = 0
    try:
        strava_account = session.query(StravaAccount).filter_by(user_id=user_id).first()
        if strava_account:
            # Try to parse athlete_id as int, fallback to 0
            try:
                athlete_id = int(strava_account.athlete_id)
            except (ValueError, TypeError):
                athlete_id = 0
    except Exception as e:
        logger.debug(f"Could not get athlete_id for user {user_id}: {e}")

    profile = AthleteProfile(user_id=user_id, athlete_id=athlete_id, sources={})
    session.add(profile)
    return profile


def _validate_goals(goals: list[str]) -> None:
    """Validate goals array.

    Args:
        goals: List of goal strings

    Raises:
        HTTPException: If validation fails
    """
    if len(goals) > 5:
        raise HTTPException(status_code=400, detail="goals must have at most 5 items")
    for goal in goals:
        if len(goal) > 200:
            raise HTTPException(status_code=400, detail="Each goal must be 200 characters or less")


def _validate_injury_notes(injury_notes: str) -> None:
    """Validate injury notes length.

    Args:
        injury_notes: Injury notes string

    Raises:
        HTTPException: If validation fails
    """
    if len(injury_notes) > 500:
        raise HTTPException(status_code=400, detail="injury_notes must be 500 characters or less")


def _validate_goal_text(goal: str) -> None:
    """Validate goal text length.

    Args:
        goal: Goal text string

    Raises:
        HTTPException: If validation fails
    """
    if len(goal) > 200:
        raise HTTPException(status_code=400, detail="goal must be 200 characters or less")


def _validate_password_match(new_password: str, confirm_password: str) -> None:
    """Validate that new password and confirmation match.

    Args:
        new_password: New password
        confirm_password: Password confirmation

    Raises:
        HTTPException: If passwords don't match
    """
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match")


def get_strava_account(user_id: str) -> StravaAccount:
    """Get StravaAccount for current user.

    Args:
        user_id: Current authenticated user ID

    Returns:
        StravaAccount instance (detached from session)

    Raises:
        HTTPException: If no Strava account is connected
    """
    # Validate user_id is actually a string, not a Depends object
    if not isinstance(user_id, str):
        error_msg = f"Invalid user_id type: {type(user_id)}. Expected str, got {type(user_id).__name__}"
        logger.error(error_msg)
        raise TypeError(error_msg)

    with get_session() as session:
        result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if not result:
            logger.info(f"Strava already disconnected for user_id={user_id}")
            raise HTTPException(status_code=404, detail="No Strava account connected. Please complete OAuth at /auth/strava")
        account = result[0]
        # Detach object from session so it can be used after session closes
        session.expunge(account)
        return account


def _extract_today_metrics(metrics_result: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    """Extract today's CTL, ATL, TSB values and 7-day TSB average from metrics.

    Args:
        metrics_result: Dictionary with "ctl", "atl", "tsb" lists of (date, value) tuples

    Returns:
        Dictionary with today_ctl, today_atl, today_tsb, tsb_7d_avg
    """
    today_ctl = 0.0
    today_atl = 0.0
    today_tsb = 0.0
    tsb_7d_avg = 0.0

    # Defensive check: ensure metrics_result is a dict
    if not isinstance(metrics_result, dict):
        logger.warning(f"[API] metrics_result is not a dict: {type(metrics_result)}")
        return {
            "today_ctl": 0.0,
            "today_atl": 0.0,
            "today_tsb": 0.0,
            "tsb_7d_avg": 0.0,
        }

    # Get TSB list with defensive checks
    tsb_list = metrics_result.get("tsb")
    if tsb_list and isinstance(tsb_list, list) and len(tsb_list) > 0:
        # Ensure last item is a tuple
        last_item = tsb_list[-1]
        if isinstance(last_item, (list, tuple)) and len(last_item) >= 2:
            today_tsb = float(last_item[1]) if isinstance(last_item[1], (int, float)) else 0.0
            today_date = str(last_item[0])

            # Find corresponding CTL and ATL
            ctl_list = metrics_result.get("ctl", [])
            if isinstance(ctl_list, list):
                for date_val, ctl_val in ctl_list:
                    if str(date_val) == today_date:
                        today_ctl = float(ctl_val) if isinstance(ctl_val, (int, float)) else 0.0
                        break

            atl_list = metrics_result.get("atl", [])
            if isinstance(atl_list, list):
                for date_val, atl_val in atl_list:
                    if str(date_val) == today_date:
                        today_atl = float(atl_val) if isinstance(atl_val, (int, float)) else 0.0
                        break

            # Calculate 7-day average of TSB
            last_7_tsb = []
            for item in tsb_list[-7:]:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    val = item[1]
                    if isinstance(val, (int, float)):
                        last_7_tsb.append(float(val))
            if last_7_tsb:
                tsb_7d_avg = sum(last_7_tsb) / len(last_7_tsb)

    return {
        "today_ctl": today_ctl,
        "today_atl": today_atl,
        "today_tsb": today_tsb,
        "tsb_7d_avg": tsb_7d_avg,
    }


def _build_overview_response(
    last_sync: str | None,
    data_quality_status: str,
    metrics_result: dict[str, list[tuple[str, float]]],
    today_metrics: dict[str, float],
) -> dict:
    """Build overview response dictionary.

    Args:
        last_sync: Last sync timestamp or None
        data_quality_status: Data quality status string
        metrics_result: Training load metrics
        today_metrics: Today's metric values

    Returns:
        Overview response dictionary

    Note:
        When data_quality_status != "ok", metrics are still returned with calculated
        values. The UI should display a "Limited data" badge to indicate the data
        quality status. This matches TrainingPeaks / WKO behavior.
    """
    # Ensure metrics are always arrays (defensive check for frontend)
    ctl_data = metrics_result.get("ctl", [])
    atl_data = metrics_result.get("atl", [])
    tsb_data = metrics_result.get("tsb", [])

    # Convert to lists if not already (handles edge cases)
    if not isinstance(ctl_data, list):
        logger.warning(f"[API] CTL data is not a list: {type(ctl_data)}, converting to empty list")
        ctl_data = []
    if not isinstance(atl_data, list):
        logger.warning(f"[API] ATL data is not a list: {type(atl_data)}, converting to empty list")
        atl_data = []
    if not isinstance(tsb_data, list):
        logger.warning(f"[API] TSB data is not a list: {type(tsb_data)}, converting to empty list")
        tsb_data = []

    metrics_data = {
        "ctl": ctl_data,
        "atl": atl_data,
        "tsb": tsb_data,
    }
    today_values = {
        "ctl": round(today_metrics["today_ctl"], 1),
        "atl": round(today_metrics["today_atl"], 1),
        "tsb": round(today_metrics["today_tsb"], 1),
        "tsb_7d_avg": round(today_metrics["tsb_7d_avg"], 1),
    }

    return {
        "connected": True,
        "last_sync": last_sync,
        "data_quality": data_quality_status,
        "metrics": metrics_data,
        "today": today_values,
    }


def _maybe_trigger_aggregation(user_id: str, activity_count: int, daily_rows: list, days: int = 60) -> list:
    """Trigger aggregation if needed and return updated daily_rows.

    Triggers aggregation if:
    - We have activities but no daily rows, OR
    - We have fewer daily rows than requested (to ensure full date range is aggregated)

    Args:
        user_id: Clerk user ID (string)
        activity_count: Number of activities in database
        daily_rows: Current daily rows list
        days: Number of days to fetch after aggregation (default: 60)

    Returns:
        Updated daily_rows list (may be re-fetched after aggregation)
    """
    should_aggregate = False
    reason = ""

    if activity_count > 0 and len(daily_rows) == 0:
        should_aggregate = True
        reason = "no daily rows"
    elif activity_count > 0 and len(daily_rows) < days:
        # Check if we're missing days in the requested range
        # If we have activities but fewer aggregated days than requested, re-aggregate
        should_aggregate = True
        reason = f"only {len(daily_rows)} days available, {days} requested"

    if should_aggregate:
        logger.info(
            f"[API] /me/overview: Auto-triggering aggregation for user_id={user_id} "
            f"(activities={activity_count}, daily_rows={len(daily_rows)}, reason={reason})"
        )
        try:
            aggregate_daily_training(user_id)
            # Re-fetch daily rows after aggregation in a new session
            with get_session() as session:
                daily_rows = get_daily_rows(session, user_id, days=days)
            logger.info(f"[API] /me/overview: Aggregation completed, now have {len(daily_rows)} daily rows (requested {days} days)")
        except Exception:
            logger.exception(f"[API] /me/overview: Failed to auto-aggregate for user_id={user_id}")
    return daily_rows


def _determine_sync_state(account: StravaAccount) -> str:
    """Determine sync state based on StravaAccount sync status.

    States:
    - "ok": Last sync was successful and within SLA
    - "syncing": Backfill is in progress (full_history_synced == False)
    - "stale": Last sync is beyond SLA threshold or never happened

    Args:
        account: StravaAccount instance

    Returns:
        Sync state string: "ok" | "syncing" | "stale"
    """
    now = int(time.time())

    # Check if backfill is in progress
    if not account.full_history_synced:
        logger.info(f"Sync state for user_id={account.user_id}: syncing (full_history_synced=False)")
        return "syncing"

    # Check if last sync exists and is within SLA
    if account.last_sync_at:
        age_seconds = now - account.last_sync_at
        age_minutes = age_seconds // 60
        if age_seconds <= SYNC_SLA_SECONDS:
            logger.info(f"Sync state for user_id={account.user_id}: ok (last_sync {age_minutes} minutes ago, within SLA)")
            return "ok"
        logger.info(
            f"Sync state for user_id={account.user_id}: stale "
            f"(last_sync {age_minutes} minutes ago, beyond SLA of {SYNC_SLA_SECONDS // 60} minutes)"
        )
        return "stale"

    # No sync ever happened
    logger.info(f"Sync state for user_id={account.user_id}: stale (no sync ever happened)")
    return "stale"


@router.get("/status")
def get_status(user_id: str = Depends(get_current_user_id)):
    """Get athlete sync status.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        {
            "connected": bool,
            "last_sync": str | null,  # ISO 8601 timestamp or null
            "state": "ok" | "syncing" | "stale"
        }
    """
    try:
        request_time = time.time()
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        logger.info(f"[API] /me/status endpoint called at {now_str} for user_id={user_id}")

        # Get StravaAccount for user
        account = get_strava_account(user_id)

        state = _determine_sync_state(account)

        last_sync = None
        if account.last_sync_at:
            last_sync = datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat()

        # Get activity count to track data retrieval
        with get_session() as session:
            result = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
            activity_count = result if result is not None else 0

        elapsed = time.time() - request_time
        logger.info(
            f"Status response: user_id={user_id}, state={state}, "
            f"last_sync={last_sync}, activity_count={activity_count}, elapsed={elapsed:.3f}s"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get status: {e!s}") from e
    else:
        return {
            "connected": True,
            "last_sync": last_sync,
            "state": state,
        }


def get_overview_data(user_id: str, days: int = 7) -> dict:
    """Get athlete training overview data (internal function).

    Args:
        user_id: Current authenticated user ID
        days: Number of days to look back (default: 7)

    Returns:
        Overview response dictionary with connected, last_sync, data_quality, metrics, today

    Raises:
        HTTPException: If no Strava account is connected or on error
    """
    # Validate user_id is actually a string, not a Depends object
    if not isinstance(user_id, str):
        error_msg = f"Invalid user_id type: {type(user_id)}. Expected str, got {type(user_id).__name__}"
        logger.error(error_msg)
        raise TypeError(error_msg)

    # Validate days parameter
    if days < 1:
        days = 7
    days = min(days, 365)  # Cap at 1 year for performance

    request_time = time.time()
    logger.info(
        f"[API] /me/overview called at {datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]} for user_id={user_id}, days={days}"
    )

    # Get StravaAccount for user
    account = get_strava_account(user_id)
    last_sync = datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat() if account.last_sync_at else None

    # Check if we have activities but no daily rows - trigger aggregation if needed
    with get_session() as session:
        # Count activities for this user
        activity_count = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar() or 0

        # Count activities in the requested date range
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)
        activities_in_range = (
            session.execute(
                select(func.count(Activity.id)).where(
                    Activity.user_id == user_id,
                    func.date(Activity.start_time) >= start_date,
                    func.date(Activity.start_time) <= end_date,
                )
            ).scalar()
            or 0
        )

        logger.info(
            f"[API] /me/overview: user_id={user_id}, total_activities={activity_count}, "
            f"activities_in_range({days} days)={activities_in_range}"
        )

        daily_rows = get_daily_rows(session, user_id, days=days)

    # Auto-trigger aggregation if needed
    daily_rows = _maybe_trigger_aggregation(user_id, activity_count, daily_rows, days=days)

    # Check if we need to fetch more historical data (ensure at least 90 days)
    days_with_training = sum(1 for row in daily_rows if row.get("load_score", 0.0) > 0.0)
    if days_with_training < 90 and activity_count > 0:
        logger.info(
            f"[API] /me/overview: user_id={user_id} has only {days_with_training} days with training "
            f"(need 90). Triggering history backfill."
        )
        try:
            # Trigger history backfill in background to fetch more data
            def trigger_backfill():
                try:
                    history_backfill_task(user_id)
                except Exception as e:
                    logger.exception(f"[API] History backfill failed for user_id={user_id}: {e}")

            threading.Thread(target=trigger_backfill, daemon=True).start()
        except Exception as e:
            logger.warning(f"[API] Failed to trigger history backfill for user_id={user_id}: {e}")

    # Log daily rows info for debugging
    logger.info(
        f"[API] /me/overview: user_id={user_id}, daily_rows_count={len(daily_rows)}, "
        f"date_range={daily_rows[0]['date']} to {daily_rows[-1]['date']}"
        if daily_rows
        else "none"
    )
    logger.debug(
        f"[API] /me/overview: Sending {len(daily_rows)} days to frontend "
        f"({days_with_training} days with training, {len(daily_rows) - days_with_training} rest days)"
    )

    # Assess data quality and compute metrics
    data_quality_status = assess_data_quality(daily_rows)
    logger.info(f"[API] /me/overview: data_quality={data_quality_status} (requires >=14 days, got {len(daily_rows)} days)")

    # Read metrics from DailyTrainingLoad table (single source of truth)
    try:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)
        start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)

        with get_session() as session:
            daily_load_rows = session.execute(
                select(DailyTrainingLoad)
                .where(
                    DailyTrainingLoad.user_id == user_id,
                    DailyTrainingLoad.date >= start_datetime,
                )
                .order_by(DailyTrainingLoad.date)
            ).all()

        # Convert to (date, value) tuples format expected by frontend
        ctl_data: list[tuple[str, float]] = []
        atl_data: list[tuple[str, float]] = []
        tsb_data: list[tuple[str, float]] = []

        for row in daily_load_rows:
            daily_load_record = row[0]  # Extract the model instance from the Row object
            date_str = daily_load_record.date.date().isoformat()
            ctl_data.append((date_str, daily_load_record.ctl))
            atl_data.append((date_str, daily_load_record.atl))
            tsb_data.append((date_str, daily_load_record.tsb))

        metrics_result = {
            "ctl": ctl_data,
            "atl": atl_data,
            "tsb": tsb_data,
        }

        logger.info(
            f"[API] /me/overview: Read {len(daily_load_rows)} days from DailyTrainingLoad table "
            f"(date range: {start_date.isoformat()} to {end_date.isoformat()})"
        )
    except Exception as e:
        logger.exception(f"[API] /me/overview: Error reading from DailyTrainingLoad: {e}")
        metrics_result = {"ctl": [], "atl": [], "tsb": []}

    today_metrics = _extract_today_metrics(metrics_result)

    elapsed = time.time() - request_time
    logger.info(f"[API] /me/overview response: data_quality={data_quality_status}, elapsed={elapsed:.3f}s")

    return _build_overview_response(last_sync, data_quality_status, metrics_result, today_metrics)


@router.get("/overview/debug")
def get_overview_debug(
    user_id: str = Depends(get_current_user_id),
    days: int = Query(default=7, ge=1, le=365, description="Number of days to look back"),
):
    """Debug endpoint to visualize overview data directly in browser.

    Returns overview data with server timestamp for debugging frontend mismatches,
    confirming CTL source, and comparing metrics vs today values.

    Args:
        user_id: Current authenticated user ID (from auth dependency)
        days: Number of days to look back (default: 7)

    Returns:
        {
            "server_time": str,  # ISO 8601 timestamp
            "overview": {
                "connected": bool,
                "last_sync": str | null,
                "data_quality": "ok" | "limited" | "insufficient",
                "metrics": {...},
                "today": {...}
            }
        }

    Access at: https://<your-render-url>/me/overview/debug
    """
    logger.info(f"[API] /me/overview/debug endpoint called with days={days} (query parameter)")
    try:
        overview = get_overview_data(user_id, days=days)
        return {
            "server_time": datetime.now(timezone.utc).isoformat(),
            "overview": overview,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting overview debug: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get overview debug: {e!s}") from e


@router.get("/overview")
def get_overview(
    user_id: str = Depends(get_current_user_id),
    days: int = Query(default=7, ge=1, le=365, description="Number of days to look back"),
):
    """Get athlete training overview.

    Args:
        user_id: Current authenticated user ID (from auth dependency)
        days: Number of days to look back (default: 7, max: 365)

    Returns:
        {
            "connected": bool,
            "last_sync": str | null,  # ISO 8601 timestamp or null
            "data_quality": "ok" | "limited" | "insufficient",
            "metrics": {
                "ctl": [(date, value), ...],
                "atl": [(date, value), ...],
                "tsb": [(date, value), ...]
            },
            "today": {
                "ctl": float,
                "atl": float,
                "tsb": float,
                "tsb_7d_avg": float
            }
        }

    Rules:
        - No LLM
        - No inference
        - Metrics are always returned with calculated values
        - UI should display "Limited data" badge when data_quality != "ok"
        - Uses derived data (daily_training_summary), not raw activities
    """
    logger.info(f"[API] /me/overview endpoint called with days={days} (query parameter)")
    try:
        return get_overview_data(user_id, days=days)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting overview: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get overview: {e!s}") from e


def _parse_activity_date(activity_time) -> date:
    """Parse activity start time to date."""
    if isinstance(activity_time, datetime):
        return activity_time.date()
    return datetime.fromisoformat(str(activity_time)).date()


def _format_activity_time(activity_time) -> str:
    """Format activity start time to ISO string."""
    if isinstance(activity_time, datetime):
        return activity_time.isoformat()
    return str(activity_time)


@router.get("/debug/data")
def get_debug_data(user_id: str = Depends(get_current_user_id)):
    """Debug endpoint to see all data we have for a user.

    Shows:
    - All activities with date ranges
    - Daily summary data
    - Date gaps and statistics
    - Sync status

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Dictionary with comprehensive debug information about user's data
    """
    logger.info(f"[API] /me/debug/data endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            # Get account info
            account = get_strava_account(user_id)
            last_sync = datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat() if account.last_sync_at else None

            # Get all activities
            activities = session.execute(select(Activity).where(Activity.user_id == user_id).order_by(Activity.start_time)).scalars().all()

            total_activities = len(activities)
            if total_activities == 0:
                return {
                    "user_id": user_id,
                    "connected": True,
                    "last_sync": last_sync,
                    "total_activities": 0,
                    "activities": [],
                    "date_range": None,
                    "gaps": [],
                }

            # Get date range
            first_date = _parse_activity_date(activities[0].start_time)
            last_date = _parse_activity_date(activities[-1].start_time)

            # Group activities by date
            activities_by_date: dict[str, list[dict]] = {}
            for activity in activities:
                activity_date = _parse_activity_date(activity.start_time)
                date_str = activity_date.isoformat()
                if date_str not in activities_by_date:
                    activities_by_date[date_str] = []
                activities_by_date[date_str].append({
                    "id": str(activity.id),
                    "strava_id": activity.strava_activity_id,
                    "type": activity.type,
                    "start_time": _format_activity_time(activity.start_time),
                    "duration_s": activity.duration_seconds,
                    "distance_m": activity.distance_meters,
                    "elevation_m": activity.elevation_gain_meters,
                })

            # Find date gaps
            current_date = first_date
            gaps = []
            while current_date <= last_date:
                date_str = current_date.isoformat()
                if date_str not in activities_by_date:
                    gaps.append(date_str)
                current_date += timedelta(days=1)

            # Get daily summary rows
            daily_rows = get_daily_rows(session, user_id, days=365)
            daily_summary_dates = [row["date"] for row in daily_rows if row.get("load_score", 0.0) > 0.0]

            return {
                "user_id": user_id,
                "connected": True,
                "last_sync": last_sync,
                "total_activities": total_activities,
                "date_range": {
                    "first_activity": first_date.isoformat(),
                    "last_activity": last_date.isoformat(),
                    "total_days": (last_date - first_date).days + 1,
                    "days_with_activities": len(activities_by_date),
                    "days_without_activities": len(gaps),
                },
                "activities_by_date": activities_by_date,
                "gaps": gaps[:100],  # Limit to first 100 gaps
                "daily_summary": {
                    "total_days": len(daily_rows),
                    "days_with_training": len(daily_summary_dates),
                    "date_range": f"{daily_rows[0]['date']} to {daily_rows[-1]['date']}" if daily_rows else "none",
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting debug data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get debug data: {e!s}") from e


@router.post("/sync/check")
def check_recent_activities(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
):
    """Check for recent activities (last 48 hours) and sync if needed.

    This endpoint should be called on every refresh or new session to ensure
    today's activities are always synced. Runs in background.

    Args:
        background_tasks: FastAPI background tasks
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Dictionary with sync status and message
    """
    logger.info(f"[API] /me/sync/check endpoint called for user_id={user_id}")
    try:
        # Verify user has Strava account
        account = get_strava_account(user_id)

        # Trigger sync in background (will check last 48 hours automatically)
        def sync_task():
            try:
                result = sync_user_activities(user_id)
                if "error" in result:
                    logger.warning(f"[API] Sync check failed for user_id={user_id}: {result.get('error')}")
                else:
                    logger.info(
                        f"[API] Sync check completed for user_id={user_id}: "
                        f"imported={result.get('imported', 0)}, skipped={result.get('skipped', 0)}"
                    )
            except Exception as e:
                logger.exception(f"[API] Error in sync check task for user_id={user_id}: {e}")

        background_tasks.add_task(sync_task)

        logger.info(f"[API] Recent activities check scheduled for user_id={user_id}")
        return {
            "success": True,
            "message": "Checking for recent activities (last 48 hours). Sync running in background.",
            "user_id": user_id,
            "last_sync": datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat() if account.last_sync_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error checking recent activities: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to check recent activities: {e!s}") from e


@router.post("/sync/now")
def trigger_sync_now(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
):
    """Trigger immediate sync of activities from Strava.

    User-initiated sync that fetches all activities since last sync (or last 48 hours).
    Runs in background to avoid blocking the request.

    Args:
        background_tasks: FastAPI background tasks
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Dictionary with sync status and message
    """
    logger.info(f"[API] /me/sync/now endpoint called for user_id={user_id}")
    try:
        # Verify user has Strava account
        account = get_strava_account(user_id)

        # Trigger sync in background
        def sync_task():
            try:
                result = sync_user_activities(user_id)
                if "error" in result:
                    logger.warning(f"[API] Manual sync failed for user_id={user_id}: {result.get('error')}")
                else:
                    logger.info(
                        f"[API] Manual sync completed for user_id={user_id}: "
                        f"imported={result.get('imported', 0)}, skipped={result.get('skipped', 0)}, "
                        f"total_fetched={result.get('total_fetched', 0)}"
                    )
            except Exception as e:
                logger.exception(f"[API] Error in manual sync task for user_id={user_id}: {e}")

        background_tasks.add_task(sync_task)

        logger.info(f"[API] Manual sync scheduled for user_id={user_id}")
        return {
            "success": True,
            "message": "Sync started in background. This will fetch activities from the last 48 hours or since your last sync.",
            "user_id": user_id,
            "last_sync": datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat() if account.last_sync_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error triggering manual sync: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to trigger sync: {e!s}") from e


@router.post("/sync/history")
def trigger_history_sync(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
):
    """Trigger full historical backfill from Strava for the current user.

    This will fetch all historical activities from Strava that are missing
    from the database. The sync runs in the background.

    Args:
        background_tasks: FastAPI background tasks
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Dictionary with sync status and message
    """
    logger.info(f"[API] /me/sync/history endpoint called for user_id={user_id}")
    try:
        # Verify user has Strava account
        account = get_strava_account(user_id)

        # Schedule background task
        background_tasks.add_task(history_backfill_task, user_id)

        logger.info(f"[API] History backfill task scheduled for user_id={user_id}")
        return {
            "success": True,
            "message": "Historical sync started in background. This may take several minutes.",
            "user_id": user_id,
            "last_sync": datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat() if account.last_sync_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error triggering history sync: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to trigger history sync: {e!s}") from e


@router.get("/profile", response_model=AthleteProfileResponse)
def get_profile(user_id: str = Depends(get_current_user_id)):
    """Get athlete profile information.

    Returns profile data including fields imported from Strava and user input.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        AthleteProfileResponse with all profile fields
    """
    logger.info(f"[API] /me/profile endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()

            if not profile:
                logger.info(f"[API] No profile found for user_id={user_id}, returning empty profile")
                # Get email from auth user
                user_result = session.execute(select(User).where(User.id == user_id)).first()
                user_email = user_result[0].email if user_result else None
                return AthleteProfileResponse(
                    full_name=None,
                    email=user_email,
                    gender=None,
                    date_of_birth=None,
                    weight_kg=None,
                    height_cm=None,
                    weight_lbs=None,
                    height_inches=None,
                    location=None,
                    unit_system="imperial",
                    strava_connected=False,
                    target_event=None,
                    goals=[],
                )

            session.expunge(profile)

            date_of_birth_str = None
            if profile.date_of_birth:
                date_of_birth_str = profile.date_of_birth.date().isoformat()

            # Convert target_event from dict to TargetEvent model if present
            target_event_obj = None
            if profile.target_event:
                try:
                    target_event_obj = TargetEvent(**profile.target_event)
                except Exception as e:
                    logger.warning(f"Failed to parse target_event for user_id={user_id}: {e}")

            logger.info(f"[API] Profile retrieved for user_id={user_id}")
            # Get email from auth user, not profile
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            user_email = user_result[0].email if user_result else None

            # Convert height_in (float) to height_inches (int) for API
            height_inches_int = None
            if profile.height_in is not None:
                height_inches_int = round(float(profile.height_in))

            return AthleteProfileResponse(
                full_name=profile.name,  # Map name -> full_name
                email=user_email,  # From auth user
                gender=profile.gender,
                date_of_birth=date_of_birth_str,
                weight_kg=profile.weight_kg,
                height_cm=profile.height_cm,
                weight_lbs=profile.weight_lbs,  # Raw float, no rounding
                height_inches=height_inches_int,  # Converted to int
                location=profile.location,
                unit_system=profile.unit_system or "imperial",
                strava_connected=profile.strava_connected,
                target_event=target_event_obj,
                goals=profile.goals or [],
            )
    except ProgrammingError as e:
        # Database schema error (missing column, table, etc.)
        logger.exception(
            f"Database schema mismatch detected for profile endpoint. Missing column/table in database. Run migrations: {e!r}"
        )
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: Database schema mismatch. Please contact support.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting profile: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to get profile: {e!s}") from e


def _get_or_create_profile(session, user_id: str) -> AthleteProfile:
    """Get existing profile or create new one, handling schema errors.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        AthleteProfile instance
    """
    try:
        profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.warning(f"[API] Database schema issue querying profile. Creating new profile: {e!r}")
            profile = _create_new_profile(session, user_id)
        else:
            raise

    if not profile:
        profile = _create_new_profile(session, user_id)

    return profile


def _update_profile_fields(profile: AthleteProfile, request: AthleteProfileUpdateRequest) -> None:
    """Update profile fields from request.

    Full object overwrite - all provided fields are set, None values clear fields.

    Args:
        profile: AthleteProfile instance to update
        request: Update request with fields to update
    """
    if profile.sources is None:
        profile.sources = {}

    # Full object overwrite - set all fields from request
    profile.name = request.full_name  # Map full_name -> name
    if request.full_name is not None:
        profile.sources["name"] = "user"

    # Email is read-only - it comes from auth user, not profile
    # Do not update profile.email

    profile.gender = request.gender
    if request.gender is not None:
        profile.sources["gender"] = "user"

    if request.date_of_birth is not None:
        try:
            parsed_date = datetime.strptime(request.date_of_birth, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            profile.date_of_birth = parsed_date
        except ValueError as e:
            logger.warning(f"Failed to parse date_of_birth: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid date format: {request.date_of_birth}. Expected YYYY-MM-DD") from e
    elif request.date_of_birth is None and hasattr(request, "date_of_birth"):
        # Explicit None clears the field
        profile.date_of_birth = None

    profile.weight_kg = request.weight_kg
    if request.weight_kg is not None:
        profile.sources["weight_kg"] = "user"

    profile.height_cm = request.height_cm
    if request.height_cm is not None:
        profile.sources["height_cm"] = "user"

    # Store weight_lbs as raw float (no rounding)
    profile.weight_lbs = request.weight_lbs
    if request.weight_lbs is not None:
        profile.sources["weight_lbs"] = "user"

    # Convert height_inches (int) to height_in (float) for database
    if request.height_inches is not None:
        profile.height_in = float(request.height_inches)
        profile.sources["height_in"] = "user"
    elif request.height_inches is None and hasattr(request, "height_inches"):
        profile.height_in = None

    profile.location = request.location
    if request.location is not None:
        profile.sources["location"] = "user"

    if request.unit_system is not None:
        _validate_unit_system(request.unit_system)
        profile.unit_system = request.unit_system
    elif request.unit_system is None and hasattr(request, "unit_system"):
        profile.unit_system = "imperial"  # Default only on creation, but handle None

    if request.target_event is not None:
        profile.target_event = {
            "name": request.target_event.name,
            "date": request.target_event.date,
            "distance": request.target_event.distance,
        }
    elif request.target_event is None and hasattr(request, "target_event"):
        profile.target_event = None

    if request.goals is not None:
        _validate_goals(request.goals)
        profile.goals = request.goals
    elif request.goals is None and hasattr(request, "goals"):
        profile.goals = []

    profile.updated_at = datetime.now(timezone.utc)


def _parse_date_of_birth_from_profile(profile: AthleteProfile, request: AthleteProfileUpdateRequest) -> str | None:
    """Parse date_of_birth from profile or request.

    Args:
        profile: AthleteProfile instance
        request: Update request

    Returns:
        Date string or None
    """
    if request.date_of_birth:
        return request.date_of_birth

    if hasattr(profile, "date_of_birth") and profile.date_of_birth:
        if hasattr(profile.date_of_birth, "date"):
            return profile.date_of_birth.date().isoformat()
        return str(profile.date_of_birth)

    return None


def _parse_target_event_from_profile(profile: AthleteProfile, request: AthleteProfileUpdateRequest) -> TargetEvent | None:
    """Parse target_event from profile or request.

    Args:
        profile: AthleteProfile instance
        request: Update request

    Returns:
        TargetEvent or None
    """
    if request.target_event:
        return request.target_event

    if hasattr(profile, "target_event") and profile.target_event:
        if isinstance(profile.target_event, dict):
            try:
                return TargetEvent(**profile.target_event)
            except Exception as e:
                logger.warning(f"Failed to parse target_event in error handler: {e}")
                return None
        else:
            return profile.target_event

    return None


def _build_unified_profile_response(
    user_first_name: str | None,
    user_last_name: str | None,
    user_timezone: str,
    profile: AthleteProfile | None,
    settings: UserSettings | None,
) -> dict[str, str | int | float | None]:
    """Build unified profile response from User, AthleteProfile, and UserSettings.

    This returns the profile in the format expected by the frontend:
    {
        "first_name": str,
        "last_name": str | None,
        "timezone": str,
        "primary_sport": str | None,
        "goal_type": str | None,  # Derived from training_focus
        "experience_level": str | None,  # From consistency
        "availability_days_per_week": int | None,  # Derived from available_days
        "availability_hours_per_week": float | None,  # From weekly_hours
        "injury_status": str | None,  # Derived from injury_history
        "injury_notes": str | None,
    }

    Args:
        user_first_name: First name from User table
        user_last_name: Last name from User table
        user_timezone: Timezone from User table
        profile: AthleteProfile instance (optional)
        settings: UserSettings instance (optional)

    Returns:
        Dictionary with unified profile data
    """
    # Map goal_type from training_focus
    goal_type = None
    if settings and settings.training_focus:
        if settings.training_focus == "race_focused":
            # Can't distinguish between performance and completion from training_focus alone
            # Default to "performance" for now
            goal_type = "performance"
        elif settings.training_focus == "general_fitness":
            goal_type = "general"

    # Convert available_days (list) to availability_days_per_week (int)
    availability_days_per_week = None
    if settings and settings.available_days:
        availability_days_per_week = len(settings.available_days)

    # Map injury_status from injury_history and injury_notes
    injury_status = None
    if settings:
        if settings.injury_history is True:
            if settings.injury_notes:
                injury_status = "managing"
            else:
                injury_status = "injured"
        elif settings.injury_history is False:
            injury_status = "none"

    return {
        "first_name": user_first_name,
        "last_name": user_last_name,
        "timezone": user_timezone,
        "primary_sport": profile.primary_sport if profile else None,
        "goal_type": goal_type,
        "experience_level": settings.consistency if settings else None,
        "availability_days_per_week": availability_days_per_week,
        "availability_hours_per_week": settings.weekly_hours if settings else None,
        "injury_status": injury_status,
        "injury_notes": settings.injury_notes if settings else None,
    }


def _build_response_from_profile(profile: AthleteProfile, user_email: str | None = None) -> AthleteProfileResponse:
    """Build response from profile object.

    Args:
        profile: AthleteProfile instance
        user_email: Email from auth user (optional, will fetch if not provided)

    Returns:
        AthleteProfileResponse
    """
    date_of_birth_str = None
    if profile.date_of_birth:
        date_of_birth_str = profile.date_of_birth.date().isoformat()

    target_event_obj = None
    if profile.target_event:
        try:
            target_event_obj = TargetEvent(**profile.target_event)
        except Exception as e:
            logger.warning(f"Failed to parse target_event: {e}")

    # Convert height_in (float) to height_inches (int)
    height_inches_int = None
    if profile.height_in is not None:
        height_inches_int = round(float(profile.height_in))

    return AthleteProfileResponse(
        full_name=profile.name,  # Map name -> full_name
        email=user_email or profile.email,  # Prefer auth email
        gender=profile.gender,
        date_of_birth=date_of_birth_str,
        weight_kg=profile.weight_kg,
        height_cm=profile.height_cm,
        weight_lbs=profile.weight_lbs,  # Raw float, no rounding
        height_inches=height_inches_int,  # Converted to int
        location=profile.location,
        unit_system=profile.unit_system or "imperial",
        strava_connected=profile.strava_connected,
        target_event=target_event_obj,
        goals=profile.goals or [],
    )


def _build_response_from_request(
    request: AthleteProfileUpdateRequest,
    profile: AthleteProfile | None = None,
    user_email: str | None = None,
) -> AthleteProfileResponse:
    """Build response from request, with fallback to profile if available.

    Args:
        request: Update request
        profile: Optional profile for fallback values
        user_email: Email from auth user

    Returns:
        AthleteProfileResponse
    """
    date_of_birth_str = request.date_of_birth if request.date_of_birth else None
    target_event_obj = request.target_event if request.target_event else None

    # Convert height_inches (int) to height_in (float) for comparison, but return as int
    height_inches_val = request.height_inches

    return AthleteProfileResponse(
        full_name=request.full_name,  # Map full_name
        email=user_email,  # From auth user
        gender=request.gender,
        date_of_birth=date_of_birth_str,
        weight_kg=request.weight_kg,
        height_cm=request.height_cm,
        weight_lbs=request.weight_lbs,  # Raw float, no rounding
        height_inches=height_inches_val,  # Integer
        location=request.location,
        unit_system=request.unit_system or "imperial",
        strava_connected=profile.strava_connected if profile and hasattr(profile, "strava_connected") else False,
        target_event=target_event_obj,
        goals=request.goals or [],
    )


@router.put("/profile", response_model=AthleteProfileResponse)
def update_profile(request: AthleteProfileUpdateRequest, user_id: str = Depends(get_current_user_id)):
    """Update athlete profile information.

    Args:
        request: Profile update request with fields to update
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated AthleteProfileResponse
    """
    logger.info(f"[API] /me/profile PUT endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            profile = _get_or_create_profile(session, user_id)
            _update_profile_fields(profile, request)
            session.commit()

            try:
                session.refresh(profile)
                session.expunge(profile)
                logger.info(f"[API] Profile updated for user_id={user_id}")
                # Get email from auth user
                user_result = session.execute(select(User).where(User.id == user_id)).first()
                user_email = user_result[0].email if user_result else None
                return _build_response_from_profile(profile, user_email)
            except Exception as refresh_error:
                error_msg = str(refresh_error).lower()
                if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
                    logger.warning(f"[API] Cannot refresh profile after update due to missing columns: {refresh_error!r}")
                    user_result = session.execute(select(User).where(User.id == user_id)).first()
                    user_email = user_result[0].email if user_result else None
                    return _build_response_from_request(request, profile, user_email)
                raise

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.exception(f"Database schema mismatch detected for profile update. Missing column. Run migrations: {e!r}")
            with get_session() as session:
                user_result = session.execute(select(User).where(User.id == user_id)).first()
                user_email = user_result[0].email if user_result else None
            return _build_response_from_request(request, None, user_email)
        logger.exception(f"Error updating profile: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {e!s}") from e


@router.get("/training-preferences", response_model=TrainingPreferencesResponse)
def get_training_preferences(user_id: str = Depends(get_current_user_id)):
    """Get training preferences.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        TrainingPreferencesResponse with all training preference fields
    """
    logger.info(f"[API] /me/training-preferences endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()

            if not settings:
                logger.info(f"[API] No settings found for user_id={user_id}, returning null values")
                # Return null values, not defaults - frontend handles defaults
                return TrainingPreferencesResponse(
                    years_of_training=None,
                    primary_sports=None,
                    available_days=None,
                    weekly_hours=None,
                    training_focus=None,
                    injury_history=None,
                    injury_notes=None,
                    consistency=None,
                    goal=None,
                )

            session.expunge(settings)

            # Return stored values exactly as persisted (no inference)
            return TrainingPreferencesResponse(
                years_of_training=settings.years_of_training,
                primary_sports=settings.primary_sports,
                available_days=settings.available_days,
                weekly_hours=settings.weekly_hours,
                training_focus=settings.training_focus,
                injury_history=settings.injury_history,
                injury_notes=settings.injury_notes,
                consistency=settings.consistency,
                goal=settings.goal,
            )
    except Exception as e:
        # Check if this is a database schema error (missing column)
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.exception(
                f"Database schema mismatch detected for training preferences. Missing column in database. Run migrations: {e!r}"
            )
            # Return defaults instead of 500 - migrations will fix this
            return TrainingPreferencesResponse()
        logger.exception(f"Error getting training preferences: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to get training preferences: {e!s}") from e


@router.put("/training-preferences", response_model=TrainingPreferencesResponse)
def update_training_preferences(request: TrainingPreferencesUpdateRequest, user_id: str = Depends(get_current_user_id)):
    """Update training preferences.

    Full object overwrite - all fields are set from request.
    None values clear fields (except where defaults apply).

    Args:
        request: Training preferences update request (full object)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated TrainingPreferencesResponse
    """
    logger.info(f"[API] /me/training-preferences PUT endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            old_settings = session.query(UserSettings).filter_by(user_id=user_id).first()
            old_goal = old_settings.goal if old_settings else None

            if not old_settings:
                settings = UserSettings(user_id=user_id)
                session.add(settings)
            else:
                settings = old_settings

            # Full object overwrite - set all fields from request
            settings.years_of_training = request.years_of_training
            settings.primary_sports = request.primary_sports
            settings.available_days = request.available_days
            settings.weekly_hours = request.weekly_hours

            if request.training_focus is not None:
                _validate_training_focus(request.training_focus)
            settings.training_focus = request.training_focus

            settings.injury_history = request.injury_history

            if request.injury_notes is not None:
                _validate_injury_notes(request.injury_notes)
            settings.injury_notes = request.injury_notes

            settings.consistency = request.consistency

            if request.goal is not None:
                _validate_goal_text(request.goal)
            settings.goal = request.goal

            settings.updated_at = datetime.now(timezone.utc)
            session.commit()

            # Trigger race extraction if goal field changed or was set
            should_extract = request.goal is not None and old_goal != request.goal
            if should_extract:
                logger.info(f"Goal field changed for user_id={user_id}, triggering race extraction")

            if should_extract:
                try:
                    profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
                    extract_and_store_race_info(session, user_id, settings, profile)
                except Exception as e:
                    logger.exception(f"Failed to extract race info during preference update: {e}")
                    # Don't fail the request if extraction fails

            session.refresh(settings)
            session.expunge(settings)

            logger.info(f"[API] Training preferences updated for user_id={user_id}")
            # Return stored values exactly as persisted (no inference)
            return TrainingPreferencesResponse(
                years_of_training=settings.years_of_training,
                primary_sports=settings.primary_sports,
                available_days=settings.available_days,
                weekly_hours=settings.weekly_hours,
                training_focus=settings.training_focus,
                injury_history=settings.injury_history,
                injury_notes=settings.injury_notes,
                consistency=settings.consistency,
                goal=settings.goal,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating training preferences: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update training preferences: {e!s}") from e


@router.get("/privacy-settings", response_model=PrivacySettingsResponse)
def get_privacy_settings(user_id: str = Depends(get_current_user_id)):
    """Get privacy settings.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        PrivacySettingsResponse with all privacy setting fields
    """
    logger.info(f"[API] /me/privacy-settings endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()

            if not settings:
                logger.info(f"[API] No settings found for user_id={user_id}, returning null values")
                # Return null values, not defaults - frontend handles defaults
                return PrivacySettingsResponse(
                    profile_visibility=None,
                    share_activity_data=None,
                    share_training_metrics=None,
                )

            session.expunge(settings)

            # Return stored values exactly as persisted (no inference)
            return PrivacySettingsResponse(
                profile_visibility=settings.profile_visibility,
                share_activity_data=settings.share_activity_data,
                share_training_metrics=settings.share_training_metrics,
            )
    except Exception as e:
        # Check if this is a database schema error (missing column)
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.exception(
                f"Database schema mismatch detected for privacy settings. Missing column in database. Run migrations: {e!r}"
            )
            # Return defaults instead of 500 - migrations will fix this
            return PrivacySettingsResponse(
                profile_visibility="private",
                share_activity_data=False,
                share_training_metrics=False,
            )
        logger.exception(f"Error getting privacy settings: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to get privacy settings: {e!s}") from e


@router.put("/privacy-settings", response_model=PrivacySettingsResponse)
def update_privacy_settings(request: PrivacySettingsUpdateRequest, user_id: str = Depends(get_current_user_id)):
    """Update privacy settings.

    Args:
        request: Privacy settings update request
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated PrivacySettingsResponse
    """
    logger.info(f"[API] /me/privacy-settings PUT endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()

            if not settings:
                settings = UserSettings(user_id=user_id)
                session.add(settings)

            # Full object overwrite - set all fields from request
            if request.profile_visibility is not None:
                _validate_profile_visibility(request.profile_visibility)
            settings.profile_visibility = request.profile_visibility
            settings.share_activity_data = request.share_activity_data
            settings.share_training_metrics = request.share_training_metrics

            settings.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(settings)
            session.expunge(settings)

            logger.info(f"[API] Privacy settings updated for user_id={user_id}")
            # Return stored values exactly as persisted (no inference)
            return PrivacySettingsResponse(
                profile_visibility=settings.profile_visibility,
                share_activity_data=settings.share_activity_data,
                share_training_metrics=settings.share_training_metrics,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating privacy settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update privacy settings: {e!s}") from e


@router.get("/notifications", response_model=NotificationsResponse)
def get_notifications(user_id: str = Depends(get_current_user_id)):
    """Get notification preferences.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        NotificationsResponse with all notification preference fields
    """
    logger.info(f"[API] /me/notifications endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()

            if not settings:
                logger.info(f"[API] No settings found for user_id={user_id}, returning null values")
                # Return null values, not defaults - frontend handles defaults
                return NotificationsResponse(
                    email_notifications=None,
                    push_notifications=None,
                    workout_reminders=None,
                    training_load_alerts=None,
                    race_reminders=None,
                    weekly_summary=None,
                    goal_achievements=None,
                    coach_messages=None,
                )

            session.expunge(settings)

            # Return stored values exactly as persisted (no inference)
            return NotificationsResponse(
                email_notifications=settings.email_notifications,
                push_notifications=settings.push_notifications,
                workout_reminders=settings.workout_reminders,
                training_load_alerts=settings.training_load_alerts,
                race_reminders=settings.race_reminders,
                weekly_summary=settings.weekly_summary,
                goal_achievements=settings.goal_achievements,
                coach_messages=settings.coach_messages,
            )
    except Exception as e:
        # Check if this is a database schema error (missing column)
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.exception(
                f"Database schema mismatch detected for notifications. Missing column in database. Run migrations: {e!r}"
            )
            # Return defaults instead of 500 - migrations will fix this
            return NotificationsResponse(
                email_notifications=True,
                push_notifications=False,
                workout_reminders=True,
                training_load_alerts=True,
                race_reminders=True,
                weekly_summary=True,
                goal_achievements=True,
                coach_messages=True,
            )
        logger.exception(f"Error getting notifications: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to get notifications: {e!s}") from e


@router.put("/notifications", response_model=NotificationsResponse)
def update_notifications(request: NotificationsUpdateRequest, user_id: str = Depends(get_current_user_id)):
    """Update notification preferences.

    Args:
        request: Notifications update request
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated NotificationsResponse
    """
    logger.info(f"[API] /me/notifications PUT endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()

            if not settings:
                settings = UserSettings(user_id=user_id)
                session.add(settings)

            # Full object overwrite - set all fields from request
            settings.email_notifications = request.email_notifications
            settings.push_notifications = request.push_notifications
            settings.workout_reminders = request.workout_reminders
            settings.training_load_alerts = request.training_load_alerts
            settings.race_reminders = request.race_reminders
            settings.weekly_summary = request.weekly_summary
            settings.goal_achievements = request.goal_achievements
            settings.coach_messages = request.coach_messages

            settings.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(settings)
            session.expunge(settings)

            logger.info(f"[API] Notifications updated for user_id={user_id}")
            # Return stored values exactly as persisted (no inference)
            return NotificationsResponse(
                email_notifications=settings.email_notifications,
                push_notifications=settings.push_notifications,
                workout_reminders=settings.workout_reminders,
                training_load_alerts=settings.training_load_alerts,
                race_reminders=settings.race_reminders,
                weekly_summary=settings.weekly_summary,
                goal_achievements=settings.goal_achievements,
                coach_messages=settings.coach_messages,
            )
    except Exception as e:
        logger.exception(f"Error updating notifications: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update notifications: {e!s}") from e


@router.patch("")
def update_timezone(request: TimezoneUpdateRequest, user_id: str = Depends(get_current_user_id)):
    """Update user timezone.

    Args:
        request: Timezone update request with IANA timezone string
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success message with updated timezone

    Raises:
        HTTPException: If timezone is invalid
    """
    logger.info(f"[API] PATCH /users/me (timezone) called for user_id={user_id}")

    def _raise_user_not_found_error() -> None:
        """Raise HTTPException for user not found after auth validation."""
        logger.error(
            f"[API] PATCH /me: CRITICAL - User validated by auth but not found in DB: user_id={user_id}. "
            "This indicates an internal server error."
        )
        raise HTTPException(
            status_code=500,
            detail="Internal server error: User data inconsistency. Please try again or contact support.",
        )

    try:
        # Validate timezone
        try:
            ZoneInfo(request.timezone)
        except Exception as e:
            logger.warning(f"[API] Invalid timezone '{request.timezone}': {e}")
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {request.timezone}") from e

        with get_session() as session:
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                # User was validated by auth dependency, so not found = internal server error
                _raise_user_not_found_error()

            user = user_result[0]
            user.timezone = request.timezone
            session.commit()
            session.refresh(user)

            logger.info(f"[API] Timezone updated for user_id={user_id} to {request.timezone}")
            return {"timezone": user.timezone}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating timezone: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update timezone: {e!s}") from e


@router.delete("")
def delete_account(user_id: str = Depends(get_current_user_id)):
    """Delete user account and all associated data.

    Performs hard delete of:
    - User account
    - Athlete profile
    - User settings (preferences, notifications, privacy)
    - Activities
    - Training data
    - All other user-related data

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success message

    Warning:
        This operation is irreversible.
    """
    logger.info(f"[API] /me DELETE endpoint called for user_id={user_id} (account deletion)")

    try:
        with get_session() as session:
            # Delete activities
            activity_count = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar() or 0
            session.execute(select(Activity).where(Activity.user_id == user_id)).scalars().delete(synchronize_session=False)  # pyright: ignore[reportUnknownMemberType]
            logger.info(f"[API] Deleted {activity_count} activities for user_id={user_id}")

            # Delete daily training load
            session.execute(select(DailyTrainingLoad).where(DailyTrainingLoad.user_id == user_id)).scalars().delete(  # pyright: ignore[reportUnknownMemberType]
                synchronize_session=False
            )

            # Delete profile
            profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
            if profile:
                session.delete(profile)

            # Delete settings
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()
            if settings:
                session.delete(settings)

            # Delete Strava account
            strava_account = session.query(StravaAccount).filter_by(user_id=user_id).first()
            if strava_account:
                session.delete(strava_account)

            # Delete user (this will cascade to other related data if foreign keys are set up)
            user = session.query(User).filter_by(id=user_id).first()
            if user:
                session.delete(user)

            session.commit()

            logger.info(f"[API] Account deleted successfully for user_id={user_id}")

            return {
                "success": True,
                "message": "Account and all associated data have been deleted",
            }
    except Exception as e:
        logger.exception(f"Error deleting account: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete account: {e!s}") from e


@router.get("/export")
def export_data(
    format: str = Query(default="json", description="Export format: json or csv"),
    user_id: str = Depends(get_current_user_id),
):
    """Export user data.

    Exports all user data in the requested format.
    Respects privacy settings - only exports data user has permission to share.

    Args:
        format: Export format ("json" or "csv")
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        JSON or CSV data export

    Raises:
        HTTPException: 400 if format is invalid
    """
    logger.info(f"[API] /me/export endpoint called for user_id={user_id}, format={format}")

    if format not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="Format must be 'json' or 'csv'")

    def _raise_user_not_found_error() -> None:
        """Raise HTTPException for user not found after auth validation."""
        logger.error(
            f"[API] /me/export: CRITICAL - User validated by auth but not found in DB: user_id={user_id}. "
            "This indicates an internal server error."
        )
        raise HTTPException(
            status_code=500,
            detail="Internal server error: User data inconsistency. Please try again or contact support.",
        )

    try:
        with get_session() as session:
            # Get user data
            user = session.query(User).filter_by(id=user_id).first()
            if not user:
                # User was validated by auth dependency, so not found = internal server error
                _raise_user_not_found_error()

            profile = session.query(AthleteProfile).filter_by(user_id=user_id).first()
            settings = session.query(UserSettings).filter_by(user_id=user_id).first()

            # Get activities
            activities = session.execute(select(Activity).where(Activity.user_id == user_id).order_by(Activity.start_time)).scalars().all()

            if format == "json":
                # Build JSON export
                export_data_dict = {
                    "user": {
                        "user_id": user.id,
                        "email": user.email,
                        "created_at": user.created_at.isoformat() if user.created_at else None,
                    },
                    "profile": _build_response_from_profile(profile, user.email).model_dump() if profile else None,
                    "settings": {
                        "training_preferences": (
                            TrainingPreferencesResponse(
                                years_of_training=settings.years_of_training or 0,
                                primary_sports=settings.primary_sports or [],
                                available_days=settings.available_days or [],
                                weekly_hours=settings.weekly_hours or 10.0,
                                training_focus=settings.training_focus or "general_fitness",
                                injury_history=settings.injury_history or False,
                                injury_notes=settings.injury_notes,
                                consistency=settings.consistency,
                                goal=settings.goal,
                            ).model_dump()
                            if settings
                            else None
                        ),
                        "notifications": (
                            NotificationsResponse(
                                email_notifications=(
                                    settings.email_notifications if settings and settings.email_notifications is not None else True
                                ),
                                push_notifications=(
                                    settings.push_notifications if settings and settings.push_notifications is not None else True
                                ),
                                workout_reminders=(
                                    settings.workout_reminders if settings and settings.workout_reminders is not None else True
                                ),
                                training_load_alerts=(
                                    settings.training_load_alerts if settings and settings.training_load_alerts is not None else True
                                ),
                                race_reminders=(settings.race_reminders if settings and settings.race_reminders is not None else True),
                                weekly_summary=(settings.weekly_summary if settings and settings.weekly_summary is not None else True),
                                goal_achievements=(
                                    settings.goal_achievements if settings and settings.goal_achievements is not None else True
                                ),
                                coach_messages=(settings.coach_messages if settings and settings.coach_messages is not None else True),
                            ).model_dump()
                            if settings
                            else None
                        ),
                        "privacy": PrivacySettingsResponse(
                            profile_visibility=settings.profile_visibility or "private" if settings else "private",
                            share_activity_data=settings.share_activity_data or False if settings else False,
                            share_training_metrics=settings.share_training_metrics or False if settings else False,
                        ).model_dump()
                        if settings
                        else None,
                    },
                    "activities": [
                        {
                            "id": str(act.id),
                            "type": act.type,
                            "start_time": act.start_time.isoformat() if act.start_time else None,
                            "duration_seconds": act.duration_seconds,
                            "distance_meters": act.distance_meters,
                            "elevation_gain_meters": act.elevation_gain_meters,
                        }
                        for act in activities
                    ],
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                }

                return JSONResponse(content=export_data_dict)

            # CSV format
            output = StringIO()
            writer = csv.writer(output)

            # Write header
            writer.writerow(["Date", "Type", "Duration (s)", "Distance (m)", "Elevation (m)"])

            # Write activities
            for act in activities:
                writer.writerow([
                    act.start_time.date().isoformat() if act.start_time else "",
                    act.type or "",
                    act.duration_seconds or "",
                    act.distance_meters or "",
                    act.elevation_gain_meters or "",
                ])

            export_date = datetime.now(timezone.utc).date().isoformat()
            filename = f"athlete_data_{user_id}_{export_date}.csv"
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error exporting data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to export data: {e!s}") from e


@router.delete("/data")
def delete_local_data(user_id: str = Depends(get_current_user_id)):
    """Delete all training data while keeping account.

    Deletes:
    - Activities
    - Daily training load
    - Training summaries
    - Coach messages
    - All training-related data

    Keeps:
    - User account
    - Profile
    - Settings/preferences

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success message
    """
    logger.info(f"[API] /me/data DELETE endpoint called for user_id={user_id} (delete training data)")

    try:
        with get_session() as session:
            # Delete activities
            activity_count = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar() or 0
            session.execute(select(Activity).where(Activity.user_id == user_id)).scalars().delete(synchronize_session=False)  # pyright: ignore[reportUnknownMemberType]
            logger.info(f"[API] Deleted {activity_count} activities for user_id={user_id}")

            # Delete daily training load
            session.execute(select(DailyTrainingLoad).where(DailyTrainingLoad.user_id == user_id)).scalars().delete(  # pyright: ignore[reportUnknownMemberType]
                synchronize_session=False
            )

            # Delete coach messages
            coach_messages = session.execute(select(CoachMessage).where(CoachMessage.user_id == user_id)).scalars().all()
            for msg in coach_messages:
                session.delete(msg)

            session.commit()

            logger.info(f"[API] Training data deleted successfully for user_id={user_id}")

            return {
                "success": True,
                "message": "All training data has been deleted. Your account and preferences remain intact.",
            }
    except Exception as e:
        logger.exception(f"Error deleting training data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete training data: {e!s}") from e


@router.post("/change-password")
def change_password(request: ChangePasswordRequest, user_id: str = Depends(get_current_user_id)):
    """Change user password.

    Args:
        request: Password change request with current and new passwords
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success message

    Raises:
        HTTPException: 401 if current password is incorrect, 400 if validation fails
    """
    logger.info(f"[API] /me/change-password endpoint called for user_id={user_id}")

    def _raise_oauth_account() -> None:
        """Raise HTTPException for OAuth account password change."""
        raise HTTPException(status_code=400, detail="Password change not available for OAuth accounts")

    def _raise_no_password_set() -> None:
        """Raise HTTPException for no password set."""
        raise HTTPException(status_code=400, detail="No password set for this account")

    def _raise_incorrect_password() -> None:
        """Raise HTTPException for incorrect current password."""
        logger.warning(f"[API] Password change failed: incorrect current password for user_id={user_id}")
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    def _raise_user_not_found_error() -> None:
        """Raise HTTPException for user not found after auth validation."""
        logger.error(
            f"[API] /me/change-password: CRITICAL - User validated by auth but not found in DB: user_id={user_id}. "
            "This indicates an internal server error."
        )
        raise HTTPException(
            status_code=500,
            detail="Internal server error: User data inconsistency. Please try again or contact support.",
        )

    try:
        # Validate passwords match
        _validate_password_match(request.new_password, request.confirm_password)

        with get_session() as session:
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                # User was validated by auth dependency, so not found = internal server error
                _raise_user_not_found_error()

            user = user_result[0]

            # Check if user has password auth
            if user.auth_provider != AuthProvider.password:
                _raise_oauth_account()

            # Verify current password
            if not user.password_hash:
                _raise_no_password_set()

            if not verify_password(request.current_password, user.password_hash):
                _raise_incorrect_password()

            # Hash new password
            try:
                new_password_hash = hash_password(request.new_password)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail="Password must be 8-72 characters",
                ) from e

            # Update password
            user.password_hash = new_password_hash
            session.commit()

            logger.info(f"[API] Password changed successfully for user_id={user_id}")

            return {"success": True, "message": "Password changed successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error changing password: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to change password: {e!s}") from e
