"""Delta computation for session links.

PHASE 3: Compute deltas between planned and actual execution.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import Activity, PlannedSession


def compute_link_deltas(
    planned_session: PlannedSession,
    activity: Activity,
) -> dict[str, float | int | None]:
    """Compute deltas between planned session and actual activity.

    Args:
        planned_session: Planned session
        activity: Actual activity

    Returns:
        Dictionary with delta values:
        - duration_seconds: actual - planned
        - distance_meters: actual - planned
        - tss: actual_tss - expected_tss (if available)
    """
    deltas: dict[str, float | int | None] = {}

    # Duration delta
    planned_duration = planned_session.duration_seconds
    actual_duration = activity.duration_seconds
    if planned_duration is not None and actual_duration is not None:
        deltas["duration_seconds"] = actual_duration - planned_duration
    else:
        deltas["duration_seconds"] = None

    # Distance delta
    planned_distance = planned_session.distance_meters
    actual_distance = activity.distance_meters
    if planned_distance is not None and actual_distance is not None:
        deltas["distance_meters"] = actual_distance - planned_distance
    else:
        deltas["distance_meters"] = None

    # TSS delta (if available)
    # Note: planned TSS would need to be computed from workout steps or estimated
    # For now, we only include actual TSS if available
    actual_tss = activity.tss
    if actual_tss is not None:
        deltas["tss"] = actual_tss
        # TODO: Compute expected TSS from planned session/workout and include delta

    return deltas


def confirm_link_with_deltas(
    session: Session,
    link_id: str,
    planned_session: PlannedSession,
    activity: Activity,
) -> None:
    """Confirm a session link and compute deltas.

    PHASE 3: When a link is confirmed, compute and store deltas.

    Args:
        session: Database session
        link_id: SessionLink ID
        planned_session: Planned session
        activity: Activity
    """
    from app.pairing.session_links import get_link_for_planned, upsert_link

    link = get_link_for_planned(session, planned_session.id)
    if not link or link.id != link_id:
        raise ValueError(f"Link {link_id} not found for planned session {planned_session.id}")

    # Compute deltas
    deltas = compute_link_deltas(planned_session, activity)

    # Update link with confirmed status, deltas, and resolved_at
    upsert_link(
        session=session,
        user_id=planned_session.user_id,
        planned_session_id=planned_session.id,
        activity_id=activity.id,
        status="confirmed",
        method=link.method,  # Preserve original method
        confidence=link.confidence,
        notes=link.notes,
        match_reason=link.match_reason,  # Preserve match_reason
        deltas=deltas,
        resolved_at=datetime.now(timezone.utc),
    )
