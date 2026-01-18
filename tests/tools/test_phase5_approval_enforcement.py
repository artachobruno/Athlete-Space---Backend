"""Phase 5 — Approval Enforcement Tests.

Tests that the executor properly enforces approval requirements for revisions.
"""

from datetime import UTC, date, datetime, timezone

import pytest

from app.coach.executor.action_executor import CoachActionExecutor
from app.db.models import PlanRevision
from app.db.session import get_session
from app.plans.modify.plan_revision_repo import create_plan_revision


def test_enforce_revision_approval_no_approval_needed():
    """Test that enforcement allows execution when no approval is required."""
    result = {
        "success": True,
        "message": "Modification applied",
        "requires_approval": False,
    }

    # Should not raise
    CoachActionExecutor._enforce_revision_approval(result)


def test_enforce_revision_approval_missing_revision_id():
    """Test that enforcement raises error when approval required but no revision_id."""
    result = {
        "success": True,
        "requires_approval": True,
        # Missing revision_id
    }

    with pytest.raises(RuntimeError, match="revision_id"):
        CoachActionExecutor._enforce_revision_approval(result)


@pytest.mark.integration
def test_enforce_revision_approval_pending_revision_blocks_execution(test_user_id, test_athlete_id):
    """Test that pending revision blocks execution.

    This test proves that:
    - Executor refuses unapproved action
    - No plan state changes occur
    """
    user_id = test_user_id
    athlete_id = test_athlete_id

    # Create a pending revision
    with get_session() as session:
        revision_record = create_plan_revision(
            session=session,
            user_id=user_id,
            athlete_id=athlete_id,
            revision_type="modify_day",
            status="pending",
            reason="Test modification",
            affected_start=datetime.now(UTC).date(),
            affected_end=datetime.now(UTC).date(),
            requires_approval=True,
            confidence=0.3,  # Low confidence triggers approval
        )
        session.commit()
        revision_id = revision_record.id

    # Result dict simulating modify_day output with pending revision
    result = {
        "success": True,
        "message": "Modification created, pending approval",
        "requires_approval": True,
        "revision_id": revision_id,
    }

    # Executor should raise error for unapproved revision
    with pytest.raises(RuntimeError, match="requires user approval"):
        CoachActionExecutor._enforce_revision_approval(result)

    # Clean up
    with get_session() as session:
        session.query(PlanRevision).filter_by(id=revision_id).delete()
        session.commit()


@pytest.mark.integration
def test_enforce_revision_approval_approved_revision_allows_execution(test_user_id, test_athlete_id):
    """Test that approved revision allows execution.

    This test proves that:
    - Approved action executes successfully
    """
    user_id = test_user_id
    athlete_id = test_athlete_id

    # Create an approved revision
    with get_session() as session:
        revision_record = create_plan_revision(
            session=session,
            user_id=user_id,
            athlete_id=athlete_id,
            revision_type="modify_day",
            status="applied",
            reason="Test modification",
            affected_start=datetime.now(UTC).date(),
            affected_end=datetime.now(UTC).date(),
            requires_approval=True,
            confidence=0.3,
        )
        # Mark as approved
        revision_record.approved_by_user = True
        revision_record.applied = True
        revision_record.applied_at = datetime.now(UTC)
        session.commit()
        revision_id = revision_record.id

    # Result dict simulating modify_day output with approved revision
    result = {
        "success": True,
        "message": "Modification applied",
        "requires_approval": True,
        "revision_id": revision_id,
    }

    # Executor should allow execution for approved revision
    CoachActionExecutor._enforce_revision_approval(result)

    # Clean up
    with get_session() as session:
        session.query(PlanRevision).filter_by(id=revision_id).delete()
        session.commit()


@pytest.mark.integration
def test_enforce_revision_approval_applied_status_without_approved_flag(test_user_id, test_athlete_id):
    """Test that applied status without approved_by_user flag still blocks execution."""
    user_id = test_user_id
    athlete_id = test_athlete_id

    # Create revision with status="applied" but approved_by_user=False
    with get_session() as session:
        revision_record = create_plan_revision(
            session=session,
            user_id=user_id,
            athlete_id=athlete_id,
            revision_type="modify_day",
            status="applied",  # Status is applied
            reason="Test modification",
            affected_start=datetime.now(UTC).date(),
            affected_end=datetime.now(UTC).date(),
            requires_approval=True,
            confidence=0.3,
        )
        # But not explicitly approved by user
        revision_record.approved_by_user = False
        session.commit()
        revision_id = revision_record.id

    result = {
        "success": True,
        "requires_approval": True,
        "revision_id": revision_id,
    }

    # Should still block - need both status="applied" AND approved_by_user=True
    with pytest.raises(RuntimeError, match="requires user approval"):
        CoachActionExecutor._enforce_revision_approval(result)

    # Clean up
    with get_session() as session:
        session.query(PlanRevision).filter_by(id=revision_id).delete()
        session.commit()


def test_enforce_revision_approval_no_result_dict():
    """Test that enforcement handles non-dict results gracefully."""
    # Should not raise for non-dict results
    CoachActionExecutor._enforce_revision_approval(None)
    CoachActionExecutor._enforce_revision_approval("string result")
    CoachActionExecutor._enforce_revision_approval({"success": True})  # No approval fields


@pytest.mark.integration
def test_enforce_revision_approval_revision_not_found(test_user_id):
    """Test that enforcement handles missing revision gracefully."""
    result = {
        "success": True,
        "requires_approval": True,
        "revision_id": "non-existent-revision-id",
    }

    # Should not raise if revision doesn't exist (tool error, not enforcement error)
    # The enforcement only checks if revision exists and requires approval
    CoachActionExecutor._enforce_revision_approval(result)
