"""Preview plan changes before confirmation.

Tier 2 - Decision tool (non-mutating).
Shows what would change if a proposal is applied.
"""

from datetime import date
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel

from datetime import timedelta

from app.tools.read.plans import get_planned_activities


class SessionChange(BaseModel):
    """A single session change in the preview."""

    session_id: str
    change_type: Literal["added", "removed", "modified"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    description: str


class PlanChangePreview(BaseModel):
    """Preview of plan changes."""

    horizon: str
    change_summary: str
    sessions_changed: list[SessionChange]
    key_sessions_changed: list[str]
    risk_notes: list[str] | None = None
    expected_impact: str | None = None


async def preview_plan_change(
    user_id: str,
    athlete_id: int,
    proposal: dict[str, Any],
    horizon: Literal["day", "week", "season", "race"],
    today: date | None = None,
) -> PlanChangePreview:
    """Preview plan changes from a proposal.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        proposal: Proposed change object (structured)
        horizon: Time horizon scope
        today: Current date (defaults to today)

    Returns:
        PlanChangePreview with diff summary
    """
    if today is None:
        from datetime import date as date_today

        today = date_today()

    logger.info(
        "Previewing plan change",
        user_id=user_id,
        athlete_id=athlete_id,
        horizon=horizon,
        proposal_type=proposal.get("type"),
    )

    # Calculate date range based on horizon
    if horizon == "day":
        start_date = today
        end_date = today
    elif horizon == "week":
        start_date = today
        end_date = today + timedelta(days=7)
    elif horizon == "season":
        start_date = today
        end_date = today + timedelta(days=90)
    else:  # race
        start_date = today
        end_date = today + timedelta(days=180)

    # Get current plan state (sync function)
    current_sessions = get_planned_activities(
        user_id=user_id,
        start=start_date,
        end=end_date,
    )

    # Build diff (v1 - simplified)
    sessions_changed: list[SessionChange] = []
    key_sessions_changed: list[str] = []

    # Extract change details from proposal
    change_type = proposal.get("type", "unknown")
    affected_sessions = proposal.get("affected_sessions", [])

    for session_id in affected_sessions:
        # Find current session
        current_session = next((s for s in current_sessions if s.id == session_id), None)

        if current_session:
            # Modified session
            sessions_changed.append(
                SessionChange(
                    session_id=session_id,
                    change_type="modified",
                    before={
                        "date": str(current_session.date),
                        "sport": current_session.sport,
                        "intensity": current_session.intensity,
                    },
                    after=proposal.get("new_session", {}),
                    description=f"Modify {current_session.sport} session on {current_session.date}",
                )
            )
            key_sessions_changed.append(f"{current_session.sport} on {current_session.date}")
        else:
            # New session
            sessions_changed.append(
                SessionChange(
                    session_id=session_id,
                    change_type="added",
                    before=None,
                    after=proposal.get("new_session", {}),
                    description=f"Add new session: {session_id}",
                )
            )

    # Build summary
    change_summary = f"Preview of {change_type} changes for {horizon}: "
    change_summary += f"{len(sessions_changed)} sessions affected"

    # Risk notes (v1 - placeholder)
    risk_notes: list[str] | None = None
    if change_type in ("volume_reduction", "intensity_cap"):
        risk_notes = ["Reduced load may impact fitness progression"]

    # Expected impact (v1 - placeholder)
    expected_impact = f"Expected impact: {change_type} will adjust training load for {horizon}"

    return PlanChangePreview(
        horizon=horizon,
        change_summary=change_summary,
        sessions_changed=sessions_changed,
        key_sessions_changed=key_sessions_changed,
        risk_notes=risk_notes,
        expected_impact=expected_impact,
    )
