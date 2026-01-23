"""Service for building season narrative summaries.

This service builds a read-only, story-driven view of how the season
is unfolding relative to the plan, week by week.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import select

from app.api.schemas.schemas import CalendarSession
from app.api.schemas.season import GoalRace, SeasonPhase, SeasonSummary, SeasonWeek
from app.calendar.view_helper import calendar_session_from_view_row, get_calendar_items_from_view
from app.coach.schemas.intent_schemas import SeasonPlan
from app.coach.utils.llm_client import CoachLLMClient
from app.db.models import User
from app.db.session import get_session
from app.pairing.session_links import get_link_for_activity, get_link_for_planned
from app.services.intelligence.store import IntentStore
from app.utils.timezone import now_user, to_utc


def _get_week_start(date_obj: date) -> date:
    """Get Monday of the week for a given date.

    Args:
        date_obj: Date to get week start for

    Returns:
        Monday date of the week
    """
    days_since_monday = date_obj.weekday()
    return date_obj - timedelta(days=days_since_monday)


def _format_date_range(week_start: date) -> str:
    """Format week date range as 'MMM d - MMM d'.

    Args:
        week_start: Monday date of the week

    Returns:
        Formatted date range string
    """
    week_end = week_start + timedelta(days=6)
    # Use day without leading zero (works on all platforms)
    start_day = week_start.day
    end_day = week_end.day
    return f"{week_start.strftime('%b')} {start_day} - {week_end.strftime('%b')} {end_day}"


def _determine_week_status(week_start: date, today: date) -> Literal["completed", "current", "upcoming"]:
    """Determine week status based on current date.

    Args:
        week_start: Monday date of the week
        today: Current date

    Returns:
        Week status
    """
    week_end = week_start + timedelta(days=6)
    if today < week_start:
        return "upcoming"
    if today > week_end:
        return "completed"
    return "current"


def _infer_phase_name(week_index: int, total_weeks: int) -> str:
    """Infer phase name based on week position in season.

    Args:
        week_index: Week number (1-based)
        total_weeks: Total weeks in season

    Returns:
        Phase name
    """
    if week_index <= total_weeks * 0.4:
        return "Base"
    if week_index <= total_weeks * 0.75:
        return "Build"
    if week_index <= total_weeks * 0.9:
        return "Peak"
    return "Taper"


def _get_phase_intent(phase_name: str) -> str:
    """Get human-readable intent for a phase.

    Args:
        phase_name: Phase name

    Returns:
        Phase intent description
    """
    intents = {
        "Base": "Build aerobic consistency and establish training routine",
        "Build": "Introduce quality and race specificity",
        "Peak": "Maximize race-specific fitness and sharpening",
        "Taper": "Reduce volume while maintaining intensity before race",
    }
    return intents.get(phase_name, "Continue training progression")


def _get_key_sessions_for_week(
    sessions: list[CalendarSession],
    week_start: date,
) -> list[str]:
    """Extract key session names for a week.

    Args:
        sessions: List of calendar sessions
        week_start: Monday date of the week

    Returns:
        List of key session names (no metrics)
    """
    week_end = week_start + timedelta(days=6)
    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    week_sessions = [
        s
        for s in sessions
        if s.date >= week_start_str and s.date <= week_end_str and s.status == "completed"
    ]

    # Extract unique session titles/types, prioritizing planned sessions
    key_sessions = []
    seen = set()
    for session in week_sessions:
        name = session.title or session.type or "Training"
        if name and name not in seen:
            key_sessions.append(name)
            seen.add(name)
            if len(key_sessions) >= 3:  # Limit to 3 key sessions
                break

    return key_sessions


def _detect_week_flags(
    sessions: list[CalendarSession],
    week_start: date,
) -> list[Literal["fatigue", "missed_sessions"]]:
    """Detect flags for a week (fatigue, missed sessions).

    Args:
        sessions: List of calendar sessions
        week_start: Monday date of the week

    Returns:
        List of flags
    """
    week_end = week_start + timedelta(days=6)
    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    week_sessions = [
        s for s in sessions if s.date >= week_start_str and s.date <= week_end_str
    ]

    flags = []

    # Check for missed sessions (planned but not completed)
    planned_count = sum(1 for s in week_sessions if s.status == "planned")
    completed_count = sum(1 for s in week_sessions if s.status == "completed")
    if planned_count > 0 and completed_count < planned_count * 0.7:
        flags.append("missed_sessions")

    # Simple fatigue detection: high volume of hard sessions
    hard_sessions = sum(
        1
        for s in week_sessions
        if s.status == "completed" and s.intensity in {"hard", "moderate"}
    )
    if hard_sessions >= 4:
        flags.append("fatigue")

    return flags


async def _generate_week_coach_summary(
    week_index: int,
    week_start: date,
    week_sessions: list[CalendarSession],
    plan_intent: str,
    phase_name: str,
) -> str:
    """Generate LLM coach summary for a week.

    Args:
        week_index: Week number in season
        week_start: Monday date of the week
        week_sessions: Sessions for this week
        plan_intent: Season plan intent/focus
        phase_name: Current phase name

    Returns:
        Coach summary (1-2 sentences)
    """
    client = CoachLLMClient()

    # Build context for LLM
    completed_sessions = [s for s in week_sessions if s.status == "completed"]
    planned_sessions = [s for s in week_sessions if s.status == "planned"]

    context = {
        "week_index": week_index,
        "week_start": week_start.isoformat(),
        "phase": phase_name,
        "plan_intent": plan_intent,
        "completed_sessions_count": len(completed_sessions),
        "planned_sessions_count": len(planned_sessions),
        "key_sessions": [s.title or s.type or "Training" for s in completed_sessions[:5] if s.title or s.type],
    }

    try:
        return await client.generate_weekly_coach_summary(context)
    except Exception as e:
        logger.error(f"Error generating week coach summary: {e}")
        # Fallback to simple summary
        if len(completed_sessions) >= len(planned_sessions) * 0.8:
            return f"Week {week_index} aligned well with the {phase_name.lower()} phase intent. Training consistency was maintained."
        if len(completed_sessions) < len(planned_sessions) * 0.5:
            return f"Week {week_index} saw reduced volume relative to plan. Recovery and consistency should be prioritized."
        return f"Week {week_index} training progressed within the {phase_name.lower()} phase framework."


async def build_season_summary(
    user_id: str,
    athlete_id: int,
) -> SeasonSummary:
    """Build season narrative summary.

    Args:
        user_id: User ID
        athlete_id: Athlete ID

    Returns:
        SeasonSummary with phases and weeks

    Raises:
        ValueError: If season plan not found
    """
    with get_session() as session:
        # Get user for timezone
        user_result = session.execute(
            select(User).where(User.id == user_id)
        ).first()
        if not user_result:
            raise ValueError("User not found")
        user = user_result[0]

        # Get current time in user's timezone
        now_local = now_user(user)

        # Get season plan
        plan_model = IntentStore.get_latest_season_plan(athlete_id, active_only=True)
        if not plan_model:
            plan_model = IntentStore.get_latest_season_plan(athlete_id, active_only=False)
        if not plan_model:
            raise ValueError("Season plan not available")

        try:
            plan = SeasonPlan(**plan_model.plan_data)
        except Exception as e:
            logger.error(f"Failed to parse season plan: {e}")
            raise ValueError("Failed to parse season plan data") from e

        # Calculate season boundaries
        season_start = plan.season_start
        season_end = plan.season_end
        total_weeks = (season_end - season_start).days // 7

        # Get goal race if available
        goal_race = None
        if plan.target_races:
            # Use first race as goal race
            race_name = plan.target_races[0]
            # Try to extract date from race name or use season_end
            race_date = season_end  # Default to season end
            goal_race = GoalRace(
                name=race_name,
                race_date=race_date,
                weeks_to_race=max(0, (race_date - now_local.date()).days // 7),
            )

        # Get calendar sessions for season period
        season_start_utc = to_utc(
            datetime.combine(season_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        )
        season_end_utc = to_utc(
            datetime.combine(season_end, datetime.max.time()).replace(tzinfo=timezone.utc)
        )

        view_rows = get_calendar_items_from_view(
            session, user_id, season_start_utc, season_end_utc
        )

        # Build pairing maps
        pairing_map: dict[str, str] = {}
        activity_pairing_map: dict[str, str] = {}

        for row in view_rows:
            item_id = str(row.get("item_id", ""))
            kind = str(row.get("kind", ""))

            if kind == "planned":
                link = get_link_for_planned(session, item_id)
                if link:
                    pairing_map[item_id] = link.activity_id
            elif kind == "activity":
                link = get_link_for_activity(session, item_id)
                if link:
                    activity_pairing_map[item_id] = link.planned_session_id

        # Filter and convert to CalendarSession
        enriched_rows = []
        for row in view_rows:
            item_id = str(row.get("item_id", ""))
            kind = str(row.get("kind", ""))
            payload = row.get("payload") or {}

            if kind == "activity" and item_id in activity_pairing_map:
                continue

            if kind == "planned" and item_id in pairing_map:
                payload = {**payload, "paired_activity_id": pairing_map[item_id]}

            enriched_row = {**row, "payload": payload}
            enriched_rows.append(enriched_row)

        all_sessions = [
            calendar_session_from_view_row(row) for row in enriched_rows
        ]
        all_sessions.sort(key=lambda s: s.date)

        # Build weeks
        weeks: list[SeasonWeek] = []
        current_phase_name = "Base"

        for week_num in range(1, total_weeks + 1):
            week_start_date = season_start + timedelta(weeks=week_num - 1)
            week_start_date = _get_week_start(week_start_date)

            phase_name = _infer_phase_name(week_num, total_weeks)
            if week_num == 1 or phase_name != _infer_phase_name(week_num - 1, total_weeks):
                current_phase_name = phase_name

            week_status = _determine_week_status(week_start_date, now_local.date())
            if week_status == "current":
                current_phase_name = phase_name

            # Get sessions for this week
            week_sessions = [
                s
                for s in all_sessions
                if s.date >= week_start_date.strftime("%Y-%m-%d")
                and s.date <= (week_start_date + timedelta(days=6)).strftime("%Y-%m-%d")
            ]

            key_sessions = _get_key_sessions_for_week(all_sessions, week_start_date)
            flags = _detect_week_flags(all_sessions, week_start_date)

            # Generate coach summary using LLM
            plan_intent = plan.focus if plan.focus else "Training progression"
            coach_summary = await _generate_week_coach_summary(
                week_index=week_num,
                week_start=week_start_date,
                week_sessions=week_sessions,
                plan_intent=plan_intent,
                phase_name=phase_name,
            )

            week = SeasonWeek(
                week_index=week_num,
                date_range=_format_date_range(week_start_date),
                status=week_status,
                coach_summary=coach_summary,
                key_sessions=key_sessions,
                flags=flags,
            )
            weeks.append(week)

        # Group weeks into phases
        phases: list[SeasonPhase] = []
        current_phase_weeks: list[SeasonWeek] = []
        current_phase: str | None = None

        for week in weeks:
            phase_name = _infer_phase_name(week.week_index, total_weeks)

            if current_phase is None or phase_name != current_phase:
                if current_phase_weeks and current_phase is not None:
                    phases.append(
                        SeasonPhase(
                            name=current_phase,
                            intent=_get_phase_intent(current_phase),
                            weeks=current_phase_weeks,
                        )
                    )
                current_phase = phase_name
                current_phase_weeks = [week]
            else:
                current_phase_weeks.append(week)

        # Add final phase
        if current_phase_weeks and current_phase is not None:
            phases.append(
                SeasonPhase(
                    name=current_phase,
                    intent=_get_phase_intent(current_phase),
                    weeks=current_phase_weeks,
                )
            )

        return SeasonSummary(
            goal_race=goal_race,
            current_phase=current_phase_name,
            phases=phases,
        )
