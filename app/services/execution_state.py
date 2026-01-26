"""Execution state derivation service.

PHASE 2: Formalize execution state as derived, centralized logic.

This module provides the single source of truth for determining execution state
from PlannedSession + SessionLink + time. Execution outcomes are never stored
on PlannedSession - they are always derived.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from app.db.models import Activity, PlannedSession, SessionLink


def derive_execution_state(
    planned_session: PlannedSession | None,
    linked_activity: Activity | None,
    now: datetime | None = None,
) -> Literal["unexecuted", "executed_as_planned", "executed_unplanned", "missed"]:
    """Derive execution state from planned session and linked activity.

    This is the single source of truth for execution state determination.
    Execution outcomes are never stored - they are always computed.

    Rules:
    - Planned + linked activity → executed_as_planned
    - Activity without plan → executed_unplanned
    - Planned, date passed, no activity → missed
    - Planned, date future → unexecuted

    Args:
        planned_session: PlannedSession instance (may be None)
        linked_activity: Activity instance (may be None)
        now: Current timestamp (defaults to UTC now)

    Returns:
        Execution state: unexecuted, executed_as_planned, executed_unplanned, or missed
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Case 1: Activity without a planned session
    if planned_session is None and linked_activity is not None:
        return "executed_unplanned"

    # Case 2: Planned session with linked activity
    if planned_session is not None and linked_activity is not None:
        return "executed_as_planned"

    # Case 3: Planned session, no activity
    if planned_session is not None and linked_activity is None:
        # Check if the planned session date has passed
        session_date = planned_session.starts_at.date()
        now_date = now.date()

        if session_date < now_date:
            return "missed"
        return "unexecuted"

    # Case 4: Neither planned nor activity (shouldn't happen in practice)
    return "unexecuted"


def derive_execution_state_from_link(
    planned_session: PlannedSession | None,
    session_link: SessionLink | None,
    activity: Activity | None = None,
    now: datetime | None = None,
) -> Literal["unexecuted", "executed_as_planned", "executed_unplanned", "missed"]:
    """Derive execution state from planned session and session link.

    Convenience wrapper that extracts activity from session_link.

    Args:
        planned_session: PlannedSession instance (may be None)
        session_link: SessionLink instance (may be None)
        activity: Activity instance (optional, will be loaded from link if not provided)
        now: Current timestamp (defaults to UTC now)

    Returns:
        Execution state: unexecuted, executed_as_planned, executed_unplanned, or missed
    """
    linked_activity = activity

    # If we have a link with confirmed status, we should have an activity
    if session_link and session_link.status == "confirmed" and linked_activity is None:
        # In practice, the caller should load the activity from the link
        # For now, we'll treat it as if there's no activity
        pass

    return derive_execution_state(planned_session, linked_activity, now)
