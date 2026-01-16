"""MODIFY → season tool.

Modifies multiple weeks (a season) of planned workouts.
Intent distribution is preserved unless explicitly overridden.
Never calls plan_week, never infers intent, never touches other weeks.
All mutations delegate to modify_week().
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import func, select

from app.coach.tools.modify_week import modify_week
from app.db.models import PlannedSession, SeasonPlan, StravaAccount
from app.db.session import get_session
from app.plans.modify.season_types import SeasonModification
from app.plans.modify.season_validators import validate_season_modification
from app.plans.modify.week_types import WeekModification

WeekChangeType = Literal["reduce_volume", "increase_volume"]


def _get_week_date_range(athlete_id: int, week_number: int, user_id: str | None = None) -> tuple[date, date]:
    """Get date range for a specific week number.

    Args:
        athlete_id: Athlete ID
        week_number: Week number (1-based)
        user_id: Optional user ID for filtering

    Returns:
        Tuple of (start_date, end_date) for the week

    Raises:
        ValueError: If no sessions found for the week
    """
    with get_session() as db:
        # Get user_id from athlete_id if not provided
        if user_id is None:
            account_result = db.execute(
                select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id)).limit(1)
            ).first()
            if account_result:
                user_id = account_result[0].user_id
            else:
                raise ValueError(f"No user_id found for athlete_id={athlete_id}")

        query = (
            select(
                func.min(PlannedSession.starts_at).label("min_date"),
                func.max(PlannedSession.starts_at).label("max_date"),
            )
            .where(
                PlannedSession.user_id == user_id,
                PlannedSession.status != "completed",
            )
        )

        result = db.execute(query).first()

        if result is None or result.min_date is None or result.max_date is None:
            # Fallback: calculate from season start date
            # Get season plan start date
            season_plan = (
                db.execute(
                    select(SeasonPlan)
                    .where(SeasonPlan.athlete_id == athlete_id, SeasonPlan.is_active.is_(True))
                    .order_by(SeasonPlan.version.desc())
                )
                .scalar_one_or_none()
            )

            if season_plan and season_plan.start_date:
                # Calculate week start (Monday) from season start
                season_start = season_plan.start_date.date()
                days_since_monday = season_start.weekday()
                first_monday = season_start - timedelta(days=days_since_monday)

                # Week 1 starts at first_monday, week N starts at first_monday + (N-1) weeks
                week_start = first_monday + timedelta(weeks=week_number - 1)
                week_end = week_start + timedelta(days=6)

                return week_start, week_end

            raise ValueError(f"No sessions found for week {week_number} and no season plan start date")

        # Convert datetime to date
        start_date = result.min_date.date()

        # Ensure we have a full week (Monday to Sunday)
        days_since_monday = start_date.weekday()
        week_start = start_date - timedelta(days=days_since_monday)
        week_end = week_start + timedelta(days=6)

        return week_start, week_end


def _get_season_weeks(athlete_id: int) -> list[int]:
    """Get list of week numbers for the active season.

    Args:
        athlete_id: Athlete ID

    Returns:
        List of week numbers (1-based)
    """
    with get_session() as db:
        # Get user_id from athlete_id
        account_result = db.execute(
            select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id)).limit(1)
        ).first()
        if not account_result:
            return []

        user_id = account_result[0].user_id

        # Get season plan to calculate week numbers from start date
        season_plan = (
            db.execute(
                select(SeasonPlan)
                .where(SeasonPlan.athlete_id == athlete_id, SeasonPlan.is_active.is_(True))
                .order_by(SeasonPlan.version.desc())
            )
            .scalar_one_or_none()
        )

        if not season_plan or not season_plan.start_date:
            # Can't calculate week numbers without season plan start date
            return []

        sessions = (
            db.execute(
                select(PlannedSession)
                .where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.status != "completed",
                )
                .distinct(PlannedSession.starts_at)
                .order_by(PlannedSession.starts_at)
            )
            .scalars()
            .all()
        )

        # Calculate week numbers from dates relative to season start
        if not season_plan.start_date:
            return []

        season_start = season_plan.start_date.date() if isinstance(season_plan.start_date, datetime) else season_plan.start_date
        days_since_monday = season_start.weekday()
        first_monday = season_start - timedelta(days=days_since_monday)

        week_numbers = set()
        for session in sessions:
            if session.starts_at:
                session_date = session.starts_at.date()
                days_diff = (session_date - first_monday).days
                if days_diff >= 0:
                    week_num = (days_diff // 7) + 1
                    week_numbers.add(week_num)

        return sorted(week_numbers)


def modify_season(
    *,
    user_id: str,
    athlete_id: int,
    modification: SeasonModification,
) -> dict:
    """Modify a season range of planned workouts.

    This tool modifies existing planned sessions across multiple weeks.
    It never regenerates, never deletes, and preserves intent by default.
    All mutations delegate to modify_week().

    Required context:
        - user_id: User ID
        - athlete_id: Athlete ID
        - modification: SeasonModification object

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        modification: SeasonModification object

    Returns:
        Dictionary with:
            - success: bool
            - message: str
            - modified_sessions: list[str] (session IDs if successful)
            - error: str (if failed)

    Raises:
        ValueError: If required fields missing or invalid modification
    """
    logger.info(
        "modify_season_started",
        user_id=user_id,
        athlete_id=athlete_id,
        change_type=modification.change_type,
        start_week=modification.start_week,
        end_week=modification.end_week,
        phase=modification.phase,
        reason=modification.reason,
    )

    # Get season weeks for validation
    season_weeks = _get_season_weeks(athlete_id)

    if not season_weeks:
        return {
            "success": False,
            "error": "No season plan found or no weeks available",
        }

    # Validate modification
    try:
        validate_season_modification(modification, season_weeks=season_weeks)
    except ValueError as e:
        return {
            "success": False,
            "error": f"Invalid modification: {e}",
        }

    # Compute weeks_to_modify ONCE after validation
    # This is the ONLY list we iterate over - never season_weeks
    weeks_to_modify = list(range(modification.start_week, modification.end_week + 1))

    # Defensive check: ensure all weeks are in the intended range
    if not all(modification.start_week <= w <= modification.end_week for w in weeks_to_modify):
        return {
            "success": False,
            "error": f"weeks_to_modify contains weeks outside range [{modification.start_week}, {modification.end_week}]",
        }

    # Collect all modified sessions
    modified_sessions: list[str] = []

    # Only volume modifications can be delegated to modify_week
    # Other change types (extend_phase, reduce_phase, shift_season, protect_race) are not yet supported
    if modification.change_type not in {"reduce_volume", "increase_volume"}:
        return {
            "success": False,
            "error": (
                f"Change type '{modification.change_type}' is not yet supported. "
                "Only 'reduce_volume' and 'increase_volume' are supported."
            ),
        }

    # Calculate weekly percent/miles if needed
    num_weeks = len(weeks_to_modify)
    weekly_percent: float | None = None
    weekly_miles: float | None = None

    if modification.percent is not None:
        # Apply same percent to each week
        weekly_percent = modification.percent
    elif modification.miles is not None:
        # Distribute miles evenly across weeks
        weekly_miles = modification.miles / num_weeks if num_weeks > 0 else modification.miles

    # Track results ONLY for weeks we execute - empty list, not sized to season
    week_results: list[dict] = []

    # ONLY loop over weeks_to_modify - never season_weeks
    for week_num in weeks_to_modify:
        # Get date range for this week
        week_start_date, week_end_date = _get_week_date_range(
            athlete_id=athlete_id,
            week_number=week_num,
            user_id=user_id,
        )

        # Build WeekModification for this week
        # Type narrowing: we know change_type is "reduce_volume" or "increase_volume" at this point
        if modification.change_type == "reduce_volume":
            week_change_type: WeekChangeType = "reduce_volume"
        elif modification.change_type == "increase_volume":
            week_change_type = "increase_volume"
        else:
            # This should never happen due to check above, but type checker needs it
            return {
                "success": False,
                "error": f"Unsupported change type: {modification.change_type}",
            }

        week_mod = WeekModification(
            change_type=week_change_type,
            start_date=week_start_date.isoformat(),
            end_date=week_end_date.isoformat(),
            percent=weekly_percent,
            miles=weekly_miles,
            reason=modification.reason,
        )

        # Delegate to modify_week
        result = modify_week(
            user_id=user_id,
            athlete_id=athlete_id,
            modification=week_mod,
        )

        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            logger.error(
                "modify_season_week_failed",
                week=week_num,
                error=error_msg,
            )
            return {
                "success": False,
                "error": f"Failed to modify week {week_num}: {error_msg}",
            }

        week_results.append(result)

        # Collect modified session IDs
        week_modified_sessions = result.get("modified_sessions", [])
        modified_sessions.extend(week_modified_sessions)

        logger.info(
            "modify_season_week_applied",
            week=week_num,
        )

    logger.info(
        "modify_season_completed",
        change_type=modification.change_type,
        start_week=modification.start_week,
        end_week=modification.end_week,
        total_weeks=len(week_results),
        total_sessions_modified=len(modified_sessions),
        reason=modification.reason,
    )

    return {
        "success": True,
        "message": f"Modified {len(week_results)} weeks ({len(modified_sessions)} sessions)",
        "modified_sessions": modified_sessions,
        "weeks_modified": weeks_to_modify,
    }
