"""MODIFY → race tool.

Modifies race-specific attributes (date, distance, priority, taper window)
without rewriting training weeks implicitly.

Core Principle: MODIFY → race never mutates sessions directly.
It only:
- updates race metadata
- optionally triggers downstream effects (re-compute taper boundaries, validation warnings)
- NEVER edits workouts automatically
"""

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.coach.adapters.race_modification_adapter import to_race_modification
from app.coach.extraction.modify_race_extractor import extract_race_modification_llm
from app.db.models import AthleteProfile
from app.db.session import get_session
from app.plans.modify.race_types import RaceModification
from app.plans.modify.race_validators import validate_race_modification
from app.plans.regenerate.regeneration_service import regenerate_plan
from app.plans.regenerate.types import RegenerationRequest


def modify_race(
    *,
    user_id: str,
    athlete_id: int,
    modification: RaceModification,
    auto_regenerate: bool = False,
) -> dict:
    """Modify race-specific attributes.

    This tool modifies race metadata only. It never edits sessions or workouts.
    Changes are applied deterministically and validation warnings are emitted.

    Required context:
        - user_id: User ID
        - athlete_id: Athlete ID
        - modification: RaceModification dict

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        modification: RaceModification object
        auto_regenerate: If True, automatically trigger plan regeneration
            after successful modification. Defaults to False.

    Returns:
        Dictionary with:
            - success: bool
            - message: str
            - race_updated: bool
            - warnings: list[str] (if any)
            - next_recommended_actions: list[str] (if any)
            - error: str (if failed)

    Raises:
        ValueError: If required fields missing or invalid modification
    """
    logger.info(
        "modify_race_started",
        user_id=user_id,
        athlete_id=athlete_id,
        change_type=modification.change_type,
        reason=modification.reason,
    )

    # Load athlete profile and validate
    athlete_profile: AthleteProfile | None = None
    old_race_date: date | None = None
    old_taper_weeks: int | None = None

    with get_session() as db:
        athlete_profile = db.execute(
            select(AthleteProfile).where(AthleteProfile.athlete_id == athlete_id)
        ).scalar_one_or_none()

        if athlete_profile is None:
            return {
                "success": False,
                "error": f"Athlete profile not found for athlete_id {athlete_id}",
            }

        # Store old values for logging
        old_race_date = athlete_profile.race_date
        old_taper_weeks = athlete_profile.taper_weeks

        # Validate modification
        try:
            today = datetime.now(timezone.utc).date()
            warnings = validate_race_modification(modification, athlete_profile, today)
        except ValueError as e:
            logger.warning(
                "modify_race_blocked",
                change_type=modification.change_type,
                reason=str(e),
            )
            return {
                "success": False,
                "error": f"Invalid modification: {e}",
            }

        # Apply modification based on change_type
        try:
            if modification.change_type == "change_date":
                if modification.new_race_date is None:
                    return {
                        "success": False,
                        "error": "change_date requires new_race_date",
                    }
                athlete_profile.race_date = modification.new_race_date
                logger.info(
                    "modify_race_applied",
                    change_type=modification.change_type,
                    old_race_date=old_race_date,
                    new_race_date=modification.new_race_date,
                )

            elif modification.change_type == "change_distance":
                if modification.new_distance_km is None:
                    return {
                        "success": False,
                        "error": "change_distance requires new_distance_km",
                    }
                # Store in target_event or extracted_race_attributes
                # For now, we'll store in extracted_race_attributes
                if athlete_profile.extracted_race_attributes is None:
                    athlete_profile.extracted_race_attributes = {}
                athlete_profile.extracted_race_attributes["distance_km"] = modification.new_distance_km
                logger.info(
                    "modify_race_applied",
                    change_type=modification.change_type,
                    new_distance_km=modification.new_distance_km,
                )

            elif modification.change_type == "change_priority":
                if modification.new_priority is None:
                    return {
                        "success": False,
                        "error": "change_priority requires new_priority",
                    }
                # Store in extracted_race_attributes
                if athlete_profile.extracted_race_attributes is None:
                    athlete_profile.extracted_race_attributes = {}
                athlete_profile.extracted_race_attributes["priority"] = modification.new_priority
                logger.info(
                    "modify_race_applied",
                    change_type=modification.change_type,
                    new_priority=modification.new_priority,
                )

            elif modification.change_type == "change_taper":
                if modification.new_taper_weeks is None:
                    return {
                        "success": False,
                        "error": "change_taper requires new_taper_weeks",
                    }
                athlete_profile.taper_weeks = modification.new_taper_weeks
                logger.info(
                    "modify_race_applied",
                    change_type=modification.change_type,
                    old_taper_weeks=old_taper_weeks,
                    new_taper_weeks=modification.new_taper_weeks,
                )

            # Persist changes
            db.commit()

            # Build impact summary
            next_recommended_actions: list[str] = []
            if modification.change_type == "change_date":
                next_recommended_actions.append("Review taper weeks")
                if warnings:
                    next_recommended_actions.append("Check for quality week overlaps")

            if modification.change_type == "change_taper":
                next_recommended_actions.append("Review taper boundaries")

            logger.info(
                "modify_race_completed",
                change_type=modification.change_type,
                warnings_count=len(warnings),
            )

        except Exception as e:
            logger.exception("MODIFY → race: Failed to apply modification")
            db.rollback()
            return {
                "success": False,
                "error": f"Failed to apply modification: {e}",
            }
        else:
            result = {
                "success": True,
                "race_updated": True,
                "message": f"Race {modification.change_type} applied successfully",
                "warnings": warnings,
                "next_recommended_actions": next_recommended_actions,
            }

            # Optional: Trigger regeneration if requested
            if auto_regenerate:
                try:
                    today = datetime.now(timezone.utc).date()
                    regen_req = RegenerationRequest(
                        start_date=today,
                        mode="partial",
                        reason=f"Auto-regenerate after modify_race: {modification.change_type}",
                    )
                    regen_revision = regenerate_plan(
                        user_id=user_id,
                        athlete_id=athlete_id,
                        req=regen_req,
                    )
                    result["regeneration_revision"] = regen_revision
                    logger.info(
                        "Auto-regeneration triggered after modify_race",
                        revision_id=regen_revision.id,
                    )
                except Exception as e:
                    logger.warning(
                        "Auto-regeneration failed after modify_race",
                        error=str(e),
                    )
                    # Don't fail the modify_race operation if regeneration fails
                    result["regeneration_error"] = str(e)

            return result
