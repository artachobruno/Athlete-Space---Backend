"""Onboarding orchestration service.

Handles the complete onboarding flow: persistence, extraction, and conditional plan generation.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.coach.schemas.intent_schemas import SeasonPlan, WeeklyIntent
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.session_planner import save_planned_sessions, season_plan_to_sessions, weekly_intent_to_sessions
from app.db.models import AthleteProfile, StravaAccount, UserSettings
from app.db.models import SeasonPlan as SeasonPlanModel
from app.db.models import WeeklyIntent as WeeklyIntentModel
from app.db.session import get_session
from app.metrics.daily_aggregation import get_daily_rows
from app.onboarding.extraction import (
    ExtractedInjuryAttributes,
    ExtractedRaceAttributes,
    GoalExtractionService,
    extract_injury_attributes,
)
from app.onboarding.persistence import persist_profile_data, persist_training_preferences
from app.onboarding.schemas import OnboardingCompleteRequest, OnboardingCompleteResponse
from app.services.intelligence.runtime import CoachRuntime
from app.services.training_preferences import extract_and_store_race_info
from app.state.api_helpers import get_training_data


@dataclass
class WeeklyIntentConfig:
    """Configuration for weekly intent generation."""

    user_id: str
    athlete_id: int
    profile: AthleteProfile
    settings: UserSettings
    extracted_attributes: dict[str, Any] | None
    extracted_injury_attributes: dict[str, Any] | None
    is_provisional: bool = False


@dataclass
class PlansConfig:
    """Configuration for plan generation."""

    user_id: str
    athlete_id: int
    profile: AthleteProfile
    settings: UserSettings
    extracted_attributes: ExtractedRaceAttributes | None
    extracted_injury_attributes: ExtractedInjuryAttributes | None


def should_generate_plan(
    session: Session,
    user_id: str,
    generate_initial_plan: bool,
) -> tuple[bool, str]:
    """Determine if plan should be generated.

    Args:
        session: Database session
        user_id: User ID
        generate_initial_plan: Whether user opted in to plan generation

    Returns:
        Tuple of (should_generate, reason)
    """
    if not generate_initial_plan:
        return (False, "user_opted_out")

    # Check if Strava is connected
    strava_account = session.query(StravaAccount).filter_by(user_id=user_id).first()
    if not strava_account:
        return (False, "strava_not_connected")

    # Check data availability
    daily_rows = get_daily_rows(session, user_id, days=14)
    days_with_training = sum(1 for row in daily_rows if row.get("load_score", 0.0) > 0.0)

    if days_with_training < 7:
        return (False, "insufficient_data")

    return (True, "ok")


def extract_race_attributes(
    profile: AthleteProfile,
    settings: UserSettings,
) -> ExtractedRaceAttributes | None:
    """Extract race attributes from goals.

    Args:
        profile: Athlete profile
        settings: User settings

    Returns:
        ExtractedRaceAttributes or None if no goals found
    """
    # Collect goal text from various sources
    goal_texts = []

    # From profile goals array
    if profile.goals:
        goal_texts.extend(profile.goals)

    # From settings goal field
    if settings.goal:
        goal_texts.append(settings.goal)

    # From target_event if available
    if profile.target_event and profile.target_event.get("name"):
        event_name = profile.target_event.get("name", "")
        event_date = profile.target_event.get("date", "")
        goal_texts.append(f"{event_name} {event_date}")

    if not goal_texts:
        logger.info("No goal text found for extraction")
        return None

    # Combine all goal texts
    combined_goal_text = " ".join(goal_texts)

    # Extract attributes
    extraction_service = GoalExtractionService()
    try:
        return extraction_service.extract_race_attributes(combined_goal_text)
    except Exception as e:
        logger.error(f"Failed to extract race attributes: {e}", exc_info=True)
        return None


def extract_injury_attributes_from_settings(
    settings: UserSettings,
) -> ExtractedInjuryAttributes | None:
    """Extract injury attributes from injury notes.

    Args:
        settings: User settings

    Returns:
        ExtractedInjuryAttributes or None if no injury notes found
    """
    if not settings.injury_notes or not settings.injury_notes.strip():
        logger.info("No injury notes found for extraction")
        return None

    try:
        return extract_injury_attributes(settings.injury_notes)
    except Exception as e:
        logger.error(f"Failed to extract injury attributes: {e}", exc_info=True)
        return None


def generate_weekly_intent_for_onboarding(
    config: WeeklyIntentConfig,
) -> WeeklyIntent | None:
    """Generate weekly intent for onboarding.

    Args:
        config: Weekly intent configuration

    Returns:
        WeeklyIntent or None if generation fails
    """
    logger.info(f"Generating weekly intent for onboarding (user_id={config.user_id}, provisional={config.is_provisional})")

    try:
        # Build context
        context = _build_weekly_intent_context(
            user_id=config.user_id,
            profile=config.profile,
            settings=config.settings,
            extracted_attributes=config.extracted_attributes,
            extracted_injury_attributes=config.extracted_injury_attributes,
            is_provisional=config.is_provisional,
        )

        # Generate intent
        runtime = CoachRuntime()
        intent = asyncio.run(
            runtime.run_weekly_intent(
                user_id=config.user_id,
                athlete_id=config.athlete_id,
                context=context,
            )
        )

        # Mark as provisional if needed
        if config.is_provisional:
            logger.info("Generated provisional weekly intent (insufficient data)")

    except Exception as e:
        logger.error(f"Failed to generate weekly intent: {e}", exc_info=True)
        return None
    else:
        return intent


def generate_season_plan_for_onboarding(
    user_id: str,
    athlete_id: int,
    profile: AthleteProfile,
    settings: UserSettings,
    *,
    extracted_attributes: dict[str, Any] | None,
    extracted_injury_attributes: dict[str, Any] | None,
) -> SeasonPlan | None:
    """Generate season plan for onboarding.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        profile: Athlete profile
        settings: User settings
        extracted_attributes: Extracted race attributes
        extracted_injury_attributes: Extracted injury attributes

    Returns:
        SeasonPlan or None if generation fails or no race exists
    """
    # Only generate if we have race information
    if not extracted_attributes or not extracted_attributes.get("event_date"):
        logger.info("No race date found, skipping season plan generation")
        return None

    logger.info(f"Generating season plan for onboarding (user_id={user_id})")

    try:
        # Build context
        context = _build_season_plan_context(
            user_id=user_id,
            profile=profile,
            settings=settings,
            extracted_attributes=extracted_attributes,
            extracted_injury_attributes=extracted_injury_attributes,
        )

        # Generate plan
        runtime = CoachRuntime()
        plan = asyncio.run(
            runtime.run_season_plan(
                user_id=user_id,
                athlete_id=athlete_id,
                context=context,
            )
        )

    except Exception as e:
        logger.error(f"Failed to generate season plan: {e}", exc_info=True)
        return None
    else:
        return plan


def save_weekly_intent(
    session: Session,
    user_id: str,
    athlete_id: int,
    intent: WeeklyIntent,
) -> None:
    """Save weekly intent to database and create planned sessions.

    Args:
        session: Database session
        user_id: User ID
        athlete_id: Athlete ID
        intent: WeeklyIntent to save
    """
    # Convert week_start date to datetime
    week_start_dt = datetime.combine(intent.week_start, datetime.min.time()).replace(tzinfo=timezone.utc)

    intent_model = WeeklyIntentModel(
        user_id=user_id,
        athlete_id=athlete_id,
        intent_data=intent.model_dump(),
        week_start=week_start_dt,
        week_number=intent.week_number,
        is_active=True,
        version=1,
    )
    session.add(intent_model)
    session.commit()

    # Create planned sessions from weekly intent (non-blocking)
    sessions = weekly_intent_to_sessions(intent)
    if sessions:
        # Use a new session for planned sessions to avoid transaction issues
        saved_count = asyncio.run(
            save_planned_sessions(
                user_id=user_id,
                athlete_id=athlete_id,
                sessions=sessions,
                plan_type="weekly",
                plan_id=intent_model.id,
            )
        )
        if saved_count > 0:
            logger.info(f"Created {saved_count} planned sessions from weekly intent for user_id={user_id}")
        else:
            logger.warning(
                f"Weekly intent saved but {len(sessions)} planned sessions could not be persisted "
                f"(service may be temporarily unavailable) for user_id={user_id}"
            )


def save_season_plan(
    session: Session,
    user_id: str,
    athlete_id: int,
    plan: SeasonPlan,
) -> None:
    """Save season plan to database and create planned sessions.

    Args:
        session: Database session
        user_id: User ID
        athlete_id: Athlete ID
        plan: SeasonPlan to save
    """
    plan_model = SeasonPlanModel(
        user_id=user_id,
        athlete_id=athlete_id,
        plan_data=plan.model_dump(),
        is_active=True,
        version=1,
    )
    session.add(plan_model)
    session.commit()

    # Create planned sessions from season plan (non-blocking)
    sessions = season_plan_to_sessions(plan)
    if sessions:
        # Use a new session for planned sessions to avoid transaction issues
        saved_count = asyncio.run(
            save_planned_sessions(
                user_id=user_id,
                athlete_id=athlete_id,
                sessions=sessions,
                plan_type="season",
                plan_id=plan_model.id,
            )
        )
        if saved_count > 0:
            logger.info(f"Created {saved_count} planned sessions from season plan for user_id={user_id}")
        else:
            logger.warning(
                f"Season plan saved but {len(sessions)} planned sessions could not be persisted "
                f"(service may be temporarily unavailable) for user_id={user_id}"
            )


def _build_weekly_intent_context(
    user_id: str,
    profile: AthleteProfile,
    settings: UserSettings,
    *,
    extracted_attributes: dict[str, Any] | None,
    extracted_injury_attributes: dict[str, Any] | None,
    is_provisional: bool,
) -> dict[str, Any]:
    """Build context for weekly intent generation.

    Args:
        user_id: User ID
        profile: Athlete profile
        settings: User settings
        extracted_attributes: Extracted race attributes
        extracted_injury_attributes: Extracted injury attributes
        is_provisional: Whether this is provisional

    Returns:
        Context dictionary
    """
    # Get current week start (Monday)
    today = datetime.now(tz=timezone.utc).date()
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)

    # Calculate age if date_of_birth is available
    age = None
    if profile.date_of_birth:
        age = (today - profile.date_of_birth.date()).days // 365

    context: dict[str, Any] = {
        "week_context": {
            "week_start": week_start.isoformat(),
            "week_number": 1,
            "time_of_year": _get_time_of_year(),
        },
        "athlete_profile": {
            # Basic info
            "name": profile.name,
            "gender": profile.gender,
            "age": age,
            "weight_kg": profile.weight_kg,
            "height_cm": profile.height_cm,
            "location": profile.location,
            "unit_system": profile.unit_system or "metric",
            # Training history
            "years_of_training": settings.years_of_training or 0,
            "primary_sports": settings.primary_sports or [],
            "available_days": settings.available_days or [],
            "weekly_hours": settings.weekly_hours or 10.0,
            "training_focus": settings.training_focus or "general_fitness",
            # Injury and health
            "injury_history": settings.injury_history or False,
            "injury_notes": settings.injury_notes,
            "consistency": settings.consistency,
            # Goals
            "goals": profile.goals or [],
            "goal_text": settings.goal,
        },
        "onboarding": True,
        "provisional": is_provisional,
    }

    # Add extracted injury information if available
    if extracted_injury_attributes:
        context["injury_info"] = {
            "injury_type": extracted_injury_attributes.get("injury_type"),
            "body_part": extracted_injury_attributes.get("body_part"),
            "severity": extracted_injury_attributes.get("severity"),
            "recovery_status": extracted_injury_attributes.get("recovery_status"),
            "restrictions": extracted_injury_attributes.get("restrictions"),
            "date_occurred": extracted_injury_attributes.get("date_occurred"),
        }
    elif settings.injury_notes:
        # Fallback to raw injury notes if extraction didn't work
        context["injury_info"] = {
            "notes": settings.injury_notes,
        }

    # Add athlete state if available
    try:
        training_data = get_training_data(user_id=user_id, days=14)
        athlete_state = build_athlete_state(
            ctl=training_data.ctl,
            atl=training_data.atl,
            tsb=training_data.tsb,
            daily_load=training_data.daily_load,
        )
        context["athlete_state"] = athlete_state.model_dump()
        context["data_confidence"] = athlete_state.confidence
    except Exception:
        # No training data - use conservative defaults
        context["athlete_state"] = None
        context["data_confidence"] = 0.3
        logger.info("No training data available, using conservative defaults")

    # Add race information - prefer extracted_attributes, fallback to profile.target_event
    race_info = None
    if extracted_attributes:
        race_info = {
            "event_type": extracted_attributes.get("event_type"),
            "event_date": extracted_attributes.get("event_date"),
            "goal_time": extracted_attributes.get("goal_time"),
            "distance": extracted_attributes.get("distance"),
            "location": extracted_attributes.get("location"),
        }
    elif profile.target_event:
        race_info = {
            "event_type": profile.target_event.get("name"),
            "event_date": profile.target_event.get("date"),
            "distance": profile.target_event.get("distance"),
        }

    if race_info:
        context["race_calendar"] = {"target_race": race_info}

    return context


def _build_season_plan_context(
    user_id: str,
    profile: AthleteProfile,
    settings: UserSettings,
    extracted_attributes: dict[str, Any] | None,
    extracted_injury_attributes: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build context for season plan generation.

    Args:
        user_id: User ID
        profile: Athlete profile
        settings: User settings
        extracted_attributes: Extracted race attributes
        extracted_injury_attributes: Extracted injury attributes

    Returns:
        Context dictionary
    """
    # Parse race date
    race_date_str = extracted_attributes.get("event_date") if extracted_attributes else None
    if not race_date_str:
        raise ValueError("Race date required for season plan")

    # Parse date (handle YYYY-MM-XX format)
    if race_date_str.endswith("-XX"):
        year_month = race_date_str[:-3]
        race_date = datetime.strptime(f"{year_month}-15", "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
    else:
        race_date = datetime.strptime(race_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()

    # Season starts 16-20 weeks before race
    season_start = race_date - timedelta(weeks=18)
    season_end = race_date

    # Calculate age if date_of_birth is available
    today = datetime.now(tz=timezone.utc).date()
    age = None
    if profile.date_of_birth:
        age = (today - profile.date_of_birth.date()).days // 365

    # Build race info - prefer extracted_attributes, fallback to profile.target_event
    race_info: dict[str, Any] = {
        "event_date": race_date.isoformat(),
    }
    if extracted_attributes:
        event_type = extracted_attributes.get("event_type")
        goal_time = extracted_attributes.get("goal_time")
        distance = extracted_attributes.get("distance")
        location = extracted_attributes.get("location")
        if event_type:
            race_info["event_type"] = event_type
        if goal_time:
            race_info["goal_time"] = goal_time
        if distance:
            race_info["distance"] = distance
        if location:
            race_info["location"] = location
    elif profile.target_event:
        event_name = profile.target_event.get("name")
        event_distance = profile.target_event.get("distance")
        if event_name:
            race_info["event_type"] = event_name
        if event_distance:
            race_info["distance"] = event_distance

    context: dict[str, Any] = {
        "season_context": {
            "season_start": season_start.isoformat(),
            "season_end": season_end.isoformat(),
            "time_of_year": _get_time_of_year(),
        },
        "race_calendar": {
            "target_race": race_info,
        },
        "athlete_goals": {
            "primary_goal": profile.primary_goal,
            "target_races": profile.target_races or [],
            "goals": profile.goals or [],
            "goal_text": settings.goal,  # Free text goal from settings
        },
        "athlete_profile": {
            # Basic info
            "name": profile.name,
            "gender": profile.gender,
            "age": age,
            "weight_kg": profile.weight_kg,
            "height_cm": profile.height_cm,
            "location": profile.location,
            "unit_system": profile.unit_system or "metric",
            # Training history
            "years_of_training": settings.years_of_training or 0,
            "primary_sports": settings.primary_sports or [],
            "available_days": settings.available_days or [],
            "weekly_hours": settings.weekly_hours or 10.0,
            "training_focus": settings.training_focus or "general_fitness",
            # Injury and health
            "injury_history": settings.injury_history or False,
            "injury_notes": settings.injury_notes,
            "consistency": settings.consistency,
        },
        "onboarding": True,
    }

    # Add extracted injury information if available
    if extracted_injury_attributes:
        context["injury_info"] = {
            "injury_type": extracted_injury_attributes.get("injury_type"),
            "body_part": extracted_injury_attributes.get("body_part"),
            "severity": extracted_injury_attributes.get("severity"),
            "recovery_status": extracted_injury_attributes.get("recovery_status"),
            "restrictions": extracted_injury_attributes.get("restrictions"),
            "date_occurred": extracted_injury_attributes.get("date_occurred"),
        }
    elif settings.injury_notes:
        # Fallback to raw injury notes if extraction didn't work
        context["injury_info"] = {
            "notes": settings.injury_notes,
        }

    # Add athlete state if available
    try:
        training_data = get_training_data(user_id=user_id, days=60)
        athlete_state = build_athlete_state(
            ctl=training_data.ctl,
            atl=training_data.atl,
            tsb=training_data.tsb,
            daily_load=training_data.daily_load,
            days_to_race=(
                (race_date - datetime.now(tz=timezone.utc).date()).days if race_date > datetime.now(tz=timezone.utc).date() else None
            ),
        )
        context["athlete_state"] = athlete_state.model_dump()
        context["training_history"] = _format_training_history_for_season(training_data.daily_load)
    except Exception:
        # No training data - use conservative defaults
        context["athlete_state"] = None
        context["training_history"] = "No historical training data available"
        logger.info("No training data available for season plan, using conservative defaults")

    return context


def _get_time_of_year() -> str:
    """Get current time of year description.

    Returns:
        Time of year string
    """
    month = datetime.now(tz=timezone.utc).month
    if month in {12, 1, 2}:
        return "winter"
    if month in {3, 4, 5}:
        return "spring"
    if month in {6, 7, 8}:
        return "summer"
    return "fall"


def _format_training_history_for_season(daily_load: list[float]) -> str:
    """Format training history for season plan context.

    Args:
        daily_load: List of daily training hours

    Returns:
        Formatted string
    """
    if not daily_load:
        return "No training history available"

    total_weeks = len(daily_load) // 7
    avg_weekly_hours = sum(daily_load) / max(total_weeks, 1)

    return f"Recent training: {total_weeks} weeks, average {avg_weekly_hours:.1f} hours/week"


def _generate_plans_for_onboarding(
    session: Session,
    config: PlansConfig,
) -> tuple[dict | None, dict | None, bool, str | None]:
    """Generate plans for onboarding.

    Args:
        session: Database session
        config: Plans configuration

    Returns:
        Tuple of (weekly_intent, season_plan, provisional, warning)
    """
    weekly_intent = None
    season_plan = None
    provisional = False
    warning = None

    # Determine if provisional (insufficient data)
    daily_rows = get_daily_rows(session, config.user_id, days=14)
    days_with_training = sum(1 for row in daily_rows if row.get("load_score", 0.0) > 0.0)
    is_provisional = days_with_training < 14

    # Generate weekly intent (always try)
    try:
        weekly_intent_config = WeeklyIntentConfig(
            user_id=config.user_id,
            athlete_id=config.athlete_id,
            profile=config.profile,
            settings=config.settings,
            extracted_attributes=config.extracted_attributes.model_dump() if config.extracted_attributes else None,
            extracted_injury_attributes=config.extracted_injury_attributes.model_dump() if config.extracted_injury_attributes else None,
            is_provisional=is_provisional,
        )
        weekly_intent_obj = generate_weekly_intent_for_onboarding(weekly_intent_config)
        if weekly_intent_obj:
            weekly_intent = weekly_intent_obj.model_dump()
            provisional = is_provisional
            save_weekly_intent(session, config.user_id, config.athlete_id, weekly_intent_obj)
    except Exception as e:
        logger.error(f"Failed to generate weekly intent: {e}", exc_info=True)
        warning = "plan_generation_failed"

    # Generate season plan (only if race exists)
    if config.extracted_attributes and config.extracted_attributes.event_date:
        try:
            season_plan_obj = generate_season_plan_for_onboarding(
                user_id=config.user_id,
                athlete_id=config.athlete_id,
                profile=config.profile,
                settings=config.settings,
                extracted_attributes=config.extracted_attributes.model_dump(),
                extracted_injury_attributes=config.extracted_injury_attributes.model_dump() if config.extracted_injury_attributes else None,
            )
            if season_plan_obj:
                season_plan = season_plan_obj.model_dump()
                save_season_plan(session, config.user_id, config.athlete_id, season_plan_obj)
        except Exception as e:
            logger.error(f"Failed to generate season plan: {e}", exc_info=True)
            if not warning:
                warning = "plan_generation_failed"

    return weekly_intent, season_plan, provisional, warning


def complete_onboarding_flow(
    user_id: str,
    request: OnboardingCompleteRequest,
) -> OnboardingCompleteResponse:
    """Complete onboarding flow - main orchestration function.

    This function orchestrates the complete onboarding process:
    1. Persists onboarding data
    2. Extracts race attributes from goals
    3. Conditionally generates plans
    4. Returns response

    Args:
        user_id: User ID
        request: Onboarding completion request

    Returns:
        OnboardingCompleteResponse with generated plans (if any)
    """
    logger.info(f"Starting onboarding flow for user_id={user_id}")

    with get_session() as session:
        try:
            # 1. Persist onboarding data
            profile = persist_profile_data(session, user_id, request.profile)
            settings = persist_training_preferences(session, user_id, request.training_preferences)

            # 2. Extract race attributes from goals using the same service as preference updates
            extract_and_store_race_info(session, user_id, settings, profile)

            # Get extracted race attributes from profile (stored by extract_and_store_race_info)
            # Refresh profile to get latest extracted_race_attributes
            session.refresh(profile)
            extracted_attributes_dict = profile.extracted_race_attributes if profile else None

            # Convert dict to ExtractedRaceAttributes object if available
            extracted_attributes_obj = None
            if extracted_attributes_dict and isinstance(extracted_attributes_dict, dict):
                with contextlib.suppress(Exception):
                    extracted_attributes_obj = ExtractedRaceAttributes(
                        event_type=extracted_attributes_dict.get("event_type"),
                        event_date=extracted_attributes_dict.get("event_date"),
                        goal_time=extracted_attributes_dict.get("target_time"),
                        distance=extracted_attributes_dict.get("distance"),
                        location=extracted_attributes_dict.get("location"),
                    )
                    # If conversion fails, leave as None (suppressed by contextlib)

            # 3. Extract injury attributes from injury notes
            extracted_injury_attributes = extract_injury_attributes_from_settings(settings)

            # 4. Store extracted injury attributes on profile
            if extracted_injury_attributes:
                profile.extracted_injury_attributes = extracted_injury_attributes.model_dump()
                session.commit()

            # 4. Conditionally generate plans
            weekly_intent = None
            season_plan = None
            provisional = False
            warning = None

            should_generate, reason = should_generate_plan(session, user_id, request.generate_initial_plan)
            if should_generate:
                logger.info(f"Generating plans for user_id={user_id}")

                # Get athlete_id
                strava_account = session.query(StravaAccount).filter_by(user_id=user_id).first()
                athlete_id = int(strava_account.athlete_id) if strava_account else 0

                plans_config = PlansConfig(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    profile=profile,
                    settings=settings,
                    extracted_attributes=extracted_attributes_obj,
                    extracted_injury_attributes=extracted_injury_attributes,
                )
                weekly_intent, season_plan, provisional, warning = _generate_plans_for_onboarding(
                    session=session,
                    config=plans_config,
                )
            else:
                logger.info(f"Skipping plan generation: {reason}")

            # 5. Mark onboarding as complete
            profile.onboarding_completed = True
            session.commit()
            session.refresh(profile)  # Refresh to ensure we have the latest state

            logger.info(f"Onboarding completed successfully for user_id={user_id}, onboarding_completed={profile.onboarding_completed}")

            return OnboardingCompleteResponse(
                status="ok",
                weekly_intent=weekly_intent,
                season_plan=season_plan,
                provisional=provisional,
                warning=warning,
            )

        except Exception as e:
            logger.error(f"Error completing onboarding: {e}", exc_info=True)
            session.rollback()
            raise
