"""Rollback engine for plan revisions.

Implements undo functionality by applying inverse diffs.
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.coach.diff.diff_models import PlanDiff
from app.db.models import PlannedSession, PlanRevision
from app.plans.modify.plan_revision_repo import create_plan_revision


def rollback_revision(
    session: Session,
    *,
    revision_id: str,
    user_id: str,
    athlete_id: int,
) -> PlanRevision:
    """Rollback a plan revision by applying its inverse diff.

    Flow:
    1. Load the revision to rollback
    2. Check if it can be rolled back (not blocked, not already rolled back)
    3. Extract diff from revision.deltas
    4. Delete sessions from after_snapshot
    5. Restore sessions from before_snapshot
    6. Create new ROLLBACK revision

    Args:
        session: Database session
        revision_id: ID of revision to rollback
        user_id: User ID performing the rollback
        athlete_id: Athlete ID whose plan is being rolled back

    Returns:
        New PlanRevision with type="rollback"

    Raises:
        ValueError: If revision cannot be rolled back
    """
    logger.info(
        "Rolling back revision",
        revision_id=revision_id,
        user_id=user_id,
        athlete_id=athlete_id,
    )

    # Step 1: Load the revision
    revision = session.execute(
        select(PlanRevision).where(PlanRevision.id == revision_id)
    ).scalar_one_or_none()

    if revision is None:
        raise ValueError(f"Revision {revision_id} not found")

    # Step 2: Check if it can be rolled back
    if revision.status == "blocked":
        raise ValueError("Cannot rollback a blocked revision")

    if revision.revision_type == "rollback":
        raise ValueError("Cannot rollback a rollback revision")

    # Check if already rolled back
    existing_rollback = session.execute(
        select(PlanRevision).where(
            PlanRevision.parent_revision_id == revision_id,
            PlanRevision.revision_type == "rollback",
        )
    ).scalar_one_or_none()

    if existing_rollback is not None:
        raise ValueError("Revision has already been rolled back")

    # Step 3: Extract diff and snapshots
    if not revision.deltas or "diff" not in revision.deltas:
        raise ValueError("Revision does not have diff data for rollback")

    diff_data = revision.deltas.get("diff")
    if not diff_data:
        raise ValueError("Revision diff is empty")

    # Parse diff
    try:
        diff = PlanDiff(**diff_data)
    except Exception as e:
        raise ValueError(f"Invalid diff format: {e}") from e

    # Step 4: Delete sessions from after_snapshot (sessions that were created/modified)
    # For added sessions, delete them
    for added_session in diff.added:
        session_to_delete = session.execute(
            select(PlannedSession).where(PlannedSession.id == added_session.session_id)
        ).scalar_one_or_none()
        if session_to_delete:
            session.delete(session_to_delete)
            logger.debug("Deleted added session", session_id=added_session.session_id)

    # For modified sessions, we need to restore the original
    # The modified sessions have new IDs, so we restore from the before state
    # This is simplified - in practice, we'd need to store full before snapshots
    for modified in diff.modified:
        # Find the current session (after modification)
        current_session = session.execute(
            select(PlannedSession).where(PlannedSession.id == modified.session_id)
        ).scalar_one_or_none()

        if current_session:
            # Restore fields from before state
            # Note: This is a simplified approach - ideally we'd have full before snapshots
            for change in modified.changes:
                # Restore the before value
                if hasattr(current_session, change.field):
                    setattr(current_session, change.field, change.before)
                    logger.debug(
                        "Restored field",
                        session_id=modified.session_id,
                        field=change.field,
                        value=change.before,
                    )

    # For removed sessions, we'd need to restore them from before_snapshot
    # This requires storing full session data in the diff, which is a future enhancement

    session.flush()

    # Step 5: Create rollback revision
    rollback_revision = create_plan_revision(
        session=session,
        user_id=user_id,
        athlete_id=athlete_id,
        revision_type="rollback",
        status="applied",
        reason=f"Rollback of revision {revision_id}",
        affected_start=revision.affected_start,
        affected_end=revision.affected_end,
        deltas={
            "rolled_back_revision_id": revision_id,
            "rolled_back_revision_type": revision.revision_type,
            "inverse_diff": {
                "added": [s.model_dump() for s in diff.removed],
                "removed": [s.model_dump() for s in diff.added],
                "modified": [
                    {
                        "session_id": m.session_id,
                        "changes": [
                            {"field": c.field, "before": c.after, "after": c.before}
                            for c in m.changes
                        ],
                    }
                    for m in diff.modified
                ],
                "unchanged": diff.unchanged,
            },
        },
        parent_revision_id=revision_id,
        confidence=1.0 - (revision.confidence or 0.5),  # Inverse confidence
        requires_approval=False,  # Rollbacks are explicit user actions
    )

    logger.info(
        "Rollback complete",
        original_revision_id=revision_id,
        rollback_revision_id=rollback_revision.id,
    )

    return rollback_revision
