"""Plan-level diffing engine.

This is the core diff engine that generates machine-readable, deterministic
diffs between two sets of PlannedSession objects.
"""

from typing import Literal

from app.coach.diff.diff_models import PlanDiff, SessionDiff
from app.coach.diff.session_diff import diff_sessions


def build_plan_diff(
    before_sessions: list,
    after_sessions: list,
    scope: Literal["day", "week", "plan"],
) -> PlanDiff:
    """Build a complete diff between two plan states.

    This function is pure (no DB, no LLM) and deterministic.

    Args:
        before_sessions: List of original PlannedSession objects
        after_sessions: List of modified PlannedSession objects
        scope: Scope of the diff ("day", "week", or "plan")

    Returns:
        PlanDiff with added, removed, modified, and unchanged sessions
    """
    before_map = {s.id: s for s in before_sessions}
    after_map = {s.id: s for s in after_sessions}

    added = []
    removed = []
    modified = []
    unchanged = []

    # Find added and modified sessions
    for sid, after_s in after_map.items():
        if sid not in before_map:
            added.append(
                SessionDiff(
                    session_id=after_s.id,
                    date=str(after_s.date),
                    type=after_s.type,
                    title=after_s.title,
                )
            )
        else:
            diff = diff_sessions(before_map[sid], after_s)
            if diff:
                modified.append(diff)
            else:
                unchanged.append(sid)

    # Find removed sessions
    for sid, before_s in before_map.items():
        if sid not in after_map:
            removed.append(
                SessionDiff(
                    session_id=before_s.id,
                    date=str(before_s.date),
                    type=before_s.type,
                    title=before_s.title,
                )
            )

    return PlanDiff(
        scope=scope,
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
    )
