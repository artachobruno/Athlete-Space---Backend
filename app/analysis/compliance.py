"""Plan compliance computation.

Compare planned sessions against completed activities.
Returns high-level reconciliation only.
"""

from app.tools.interfaces import CompletedActivity, PlannedSession


def compute_plan_compliance(
    planned: list[PlannedSession],
    completed: list[CompletedActivity],
) -> dict:
    """Compare planned sessions against completed activities.

    Returns high-level reconciliation only.
    No recommendations, no mutations.

    Args:
        planned: List of planned sessions
        completed: List of completed activities

    Returns:
        Dictionary with compliance metrics:
        - planned_count: Number of planned sessions
        - completed_count: Number of completed sessions
        - missed_sessions: List of planned session IDs that were not completed
        - completion_pct: Completion percentage (0.0 to 1.0)
        - planned_load: Total planned load (sum of target_load)
        - completed_load: Total completed load (sum of load for matched activities)
        - load_delta: Difference between completed and planned load
    """
    planned_by_id = {p.id: p for p in planned}
    completed_by_plan_id = {
        a.planned_session_id: a
        for a in completed
        if a.planned_session_id
    }

    completed_ids = set(completed_by_plan_id.keys())
    planned_ids = set(planned_by_id.keys())

    missed = planned_ids - completed_ids
    completed_ok = planned_ids & completed_ids

    load_planned = sum(p.target_load for p in planned)
    load_completed = sum(a.load for a in completed if a.planned_session_id)

    return {
        "planned_count": len(planned),
        "completed_count": len(completed_ok),
        "missed_sessions": list(missed),
        "completion_pct": (
            len(completed_ok) / len(planned) if planned else 1.0
        ),
        "planned_load": load_planned,
        "completed_load": load_completed,
        "load_delta": load_completed - load_planned,
    }
