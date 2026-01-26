"""Weekly execution summary builder.

PHASE A + B: Build weekly summary card from execution summaries.

This module provides deterministic aggregation and templated narrative generation
for weekly summary cards. No LLM calls - pure derived data + templates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import TypedDict

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.pairing.session_links import get_link_for_activity, get_link_for_planned
from app.services.execution_state import derive_execution_state
from app.services.workout_execution_service import get_execution_summary


class WeeklySummaryCardData(TypedDict):
    """Type definition for weekly summary card data."""

    narrative: str
    total_planned_sessions: int
    executed_as_planned_count: int
    missed_sessions_count: int
    unplanned_sessions_count: int
    strongest_session_id: str | None
    strongest_session_narrative: str | None
    week_start: str
    week_end: str


class WeeklyExecutionSummaryContext:
    """Aggregated execution summary context for a week.

    PHASE A: Deterministic aggregation from execution summaries.
    """

    def __init__(
        self,
        total_planned_sessions: int,
        executed_as_planned_count: int,
        missed_sessions_count: int,
        unplanned_sessions_count: int,
        strongest_session_id: str | None = None,
        strongest_session_narrative: str | None = None,
        key_session_summaries: list[dict[str, str | float | None]] | None = None,
    ):
        self.total_planned_sessions = total_planned_sessions
        self.executed_as_planned_count = executed_as_planned_count
        self.missed_sessions_count = missed_sessions_count
        self.unplanned_sessions_count = unplanned_sessions_count
        self.strongest_session_id = strongest_session_id
        self.strongest_session_narrative = strongest_session_narrative
        self.key_session_summaries = key_session_summaries or []


def build_weekly_execution_summary_context(
    session: Session,
    user_id: str,
    week_start: date,
    week_end: date,
) -> WeeklyExecutionSummaryContext:
    """Build weekly execution summary context from execution summaries.

    PHASE A: Deterministic aggregation - no LLM, no recomputation.

    Args:
        session: Database session
        user_id: User ID
        week_start: Week start date (Monday)
        week_end: Week end date (Sunday)

    Returns:
        WeeklyExecutionSummaryContext with aggregated facts
    """
    week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    week_end_dt = datetime.combine(week_end, datetime.max.time()).replace(tzinfo=timezone.utc)

    # Get all planned sessions for the week
    planned_sessions = (
        session.execute(
            select(PlannedSession).where(
                PlannedSession.user_id == user_id,
                PlannedSession.starts_at >= week_start_dt,
                PlannedSession.starts_at <= week_end_dt,
                PlannedSession.lifecycle_status != "cancelled",
            )
        )
        .scalars()
        .all()
    )

    total_planned = len(planned_sessions)
    executed_as_planned = 0
    missed_sessions = 0
    unplanned_count = 0

    # Track strongest session (highest compliance or highest positive TSS delta)
    strongest_session_id = None
    strongest_session_narrative = None
    strongest_score = -1.0

    key_session_summaries: list[dict[str, str | float | None]] = []
    now_utc = datetime.now(timezone.utc)

    # Process each planned session
    for planned in planned_sessions:
        # Get session link
        session_link = get_link_for_planned(session, planned.id)
        linked_activity = None

        if session_link and session_link.status in {"confirmed", "proposed"}:
            linked_activity = session.get(Activity, session_link.activity_id)

        # Compute execution state
        execution_state = derive_execution_state(planned, linked_activity, now_utc)

        if execution_state == "executed_as_planned":
            executed_as_planned += 1

            # Get execution summary if available
            if linked_activity:
                summary = get_execution_summary(session, linked_activity.id)
                if summary:
                    # Track strongest session
                    score = summary.compliance_score or 0.0
                    # If no compliance score, use positive deltas as proxy
                    if score == 0.0 and summary.step_comparison:
                        # Use step comparison as proxy for strength
                        score = 0.5  # Default for sessions with step data

                    if score > strongest_score:
                        strongest_score = score
                        strongest_session_id = linked_activity.id
                        strongest_session_narrative = summary.narrative

                    # Collect key session summaries
                    key_session_summaries.append(
                        {
                            "activity_id": linked_activity.id,
                            "planned_session_id": planned.id,
                            "narrative": summary.narrative,
                            "compliance_score": summary.compliance_score,
                            "session_type": planned.session_type,
                            "title": planned.title,
                        }
                    )
        elif execution_state == "missed":
            missed_sessions += 1

    # Get unplanned activities (activities not linked to any planned session)
    all_activities = (
        session.execute(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.starts_at >= week_start_dt,
                Activity.starts_at <= week_end_dt,
            )
        )
        .scalars()
        .all()
    )

    # Count unplanned activities (not linked to any planned session)
    for activity in all_activities:
        # Check if this activity is linked to any planned session
        link = get_link_for_activity(session, activity.id)
        if not link or link.status not in {"confirmed", "proposed"}:
            unplanned_count += 1

            # Get execution summary for unplanned activity
            summary = get_execution_summary(session, activity.id)
            if summary and summary.narrative:
                key_session_summaries.append(
                    {
                        "activity_id": activity.id,
                        "planned_session_id": None,
                        "narrative": summary.narrative,
                        "compliance_score": summary.compliance_score,
                        "session_type": activity.sport_type,
                        "title": activity.name or "Unplanned session",
                    }
                )

    return WeeklyExecutionSummaryContext(
        total_planned_sessions=total_planned,
        executed_as_planned_count=executed_as_planned,
        missed_sessions_count=missed_sessions,
        unplanned_sessions_count=unplanned_count,
        strongest_session_id=strongest_session_id,
        strongest_session_narrative=strongest_session_narrative,
        key_session_summaries=key_session_summaries,
    )


def build_weekly_summary_narrative(
    context: WeeklyExecutionSummaryContext,
    _week_start: date,
) -> str:
    """Build templated narrative from weekly execution summary context.

    PHASE B: Templated narrative synthesis - no LLM, deterministic.

    Args:
        context: WeeklyExecutionSummaryContext
        week_start: Week start date (for context)

    Returns:
        Narrative string (1-2 sentences max)
    """
    # Determine primary focus from execution patterns
    execution_rate = (
        context.executed_as_planned_count / context.total_planned_sessions
        if context.total_planned_sessions > 0
        else 0.0
    )

    # Build narrative components
    parts: list[str] = []

    # Execution adherence
    if context.total_planned_sessions > 0:
        if execution_rate >= 0.8:
            parts.append(f"You executed {context.executed_as_planned_count} of {context.total_planned_sessions} planned sessions")
        elif execution_rate >= 0.5:
            parts.append(f"You completed {context.executed_as_planned_count} of {context.total_planned_sessions} planned sessions")
        else:
            parts.append(f"You completed {context.executed_as_planned_count} of {context.total_planned_sessions} planned sessions")

        if context.missed_sessions_count > 0:
            parts.append(f"with {context.missed_sessions_count} missed")

    # Unplanned sessions
    if context.unplanned_sessions_count > 0:
        if context.unplanned_sessions_count == 1:
            parts.append("plus 1 unplanned session")
        else:
            parts.append(f"plus {context.unplanned_sessions_count} unplanned sessions")

    # Strongest session highlight
    if context.strongest_session_narrative:
        # Extract key phrase from narrative (first sentence or key phrase)
        narrative_parts = context.strongest_session_narrative.split(".")
        if narrative_parts:
            strongest_phrase = narrative_parts[0].strip()
            # Simplify if too long
            if len(strongest_phrase) > 60:
                strongest_phrase = strongest_phrase[:57] + "..."
            parts.append(f"with {strongest_phrase.lower()}")

    # Combine into 1-2 sentences
    if not parts:
        return "This week's training summary is being prepared."

    # First sentence: execution summary (first 2 parts)
    execution_parts = parts[:2] if len(parts) >= 2 else parts
    first_sentence = "This week, " + ", ".join(execution_parts) + "."

    # Second sentence: strongest session (if available and not already included)
    if len(parts) > 2:
        strongest_part = parts[2]
        # Capitalize first letter, ensure it ends with period
        if not strongest_part.endswith("."):
            strongest_part += "."
        second_sentence = strongest_part[0].upper() + strongest_part[1:] if len(strongest_part) > 1 else strongest_part
        return f"{first_sentence} {second_sentence}"

    return first_sentence


def build_weekly_summary_card(
    session: Session,
    user_id: str,
    week_start: date,
) -> WeeklySummaryCardData:
    """Build complete weekly summary card data.

    PHASE A + B: Aggregation + narrative synthesis.

    Args:
        session: Database session
        user_id: User ID
        week_start: Week start date (Monday)

    Returns:
        Dictionary with summary card data:
        - narrative: Templated narrative (1-2 sentences)
        - total_planned_sessions: Total planned sessions
        - executed_as_planned_count: Sessions executed as planned
        - missed_sessions_count: Missed sessions
        - unplanned_sessions_count: Unplanned activities
        - strongest_session_id: ID of strongest session (if available)
        - strongest_session_narrative: Narrative for strongest session
    """
    # Calculate week end (Sunday)
    week_end = week_start + timedelta(days=6)

    # PHASE A: Aggregate execution summaries
    context = build_weekly_execution_summary_context(session, user_id, week_start, week_end)

    # PHASE B: Generate templated narrative
    narrative = build_weekly_summary_narrative(context, week_start)

    return {
        "narrative": narrative,
        "total_planned_sessions": context.total_planned_sessions,
        "executed_as_planned_count": context.executed_as_planned_count,
        "missed_sessions_count": context.missed_sessions_count,
        "unplanned_sessions_count": context.unplanned_sessions_count,
        "strongest_session_id": context.strongest_session_id,
        "strongest_session_narrative": context.strongest_session_narrative,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
    }
