"""Tests for plan revision persistence.

Tests that plan revisions are correctly persisted for both applied and blocked modifications.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import PlannedSession, PlanRevision
from app.db.session import get_session
from app.plans.modify.plan_revision_repo import create_plan_revision, list_plan_revisions


def test_create_applied_revision(test_user_id: str, test_athlete_id: int) -> None:
    """Test that an applied revision is correctly persisted."""
    with get_session() as db:
        revision = create_plan_revision(
            session=db,
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            revision_type="modify_day",
            status="applied",
            reason="Reduce distance",
            affected_start=date(2024, 1, 1),
            affected_end=date(2024, 1, 1),
            deltas={
                "before": {"distance_mi": 5.0},
                "after": {"distance_mi": 4.0},
            },
        )
        db.commit()

        # Verify revision was created
        assert revision.id is not None
        assert revision.user_id == test_user_id
        assert revision.athlete_id == test_athlete_id
        assert revision.revision_type == "modify_day"
        assert revision.status == "applied"
        assert revision.reason == "Reduce distance"
        assert revision.affected_start == date(2024, 1, 1)
        assert revision.affected_end == date(2024, 1, 1)
        assert revision.deltas is not None
        assert revision.deltas["before"]["distance_mi"] == 5.0
        assert revision.deltas["after"]["distance_mi"] == 4.0

        # Verify it can be queried
        query = select(PlanRevision).where(PlanRevision.id == revision.id)
        found = db.execute(query).scalar_one()
        assert found.id == revision.id
        assert found.status == "applied"


def test_create_blocked_revision(test_user_id: str, test_athlete_id: int) -> None:
    """Test that a blocked revision is correctly persisted."""
    with get_session() as db:
        revision = create_plan_revision(
            session=db,
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            revision_type="modify_day",
            status="blocked",
            reason="Reduce distance",
            blocked_reason="Race day protection",
            affected_start=date(2024, 1, 1),
            affected_end=date(2024, 1, 1),
        )
        db.commit()

        # Verify revision was created
        assert revision.id is not None
        assert revision.status == "blocked"
        assert revision.blocked_reason == "Race day protection"
        assert revision.deltas is None or revision.deltas.get("before") is None


def test_list_plan_revisions(test_user_id: str, test_athlete_id: int) -> None:
    """Test that revisions can be listed for an athlete."""
    with get_session() as db:
        # Create multiple revisions
        revision1 = create_plan_revision(
            session=db,
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            revision_type="modify_day",
            status="applied",
            reason="Test 1",
        )
        db.flush()

        revision2 = create_plan_revision(
            session=db,
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            revision_type="modify_week",
            status="applied",
            reason="Test 2",
        )
        db.commit()

        # List revisions
        revisions = list_plan_revisions(session=db, athlete_id=test_athlete_id)

        # Verify we got both revisions, ordered by created_at DESC
        assert len(revisions) >= 2
        assert revisions[0].id == revision2.id  # Newest first
        assert revisions[1].id == revision1.id


def test_revision_deltas_stored(test_user_id: str, test_athlete_id: int) -> None:
    """Test that deltas are correctly stored in JSON format."""
    with get_session() as db:
        deltas = {
            "before": {
                "session_id": "s1",
                "distance_mi": 5.0,
            },
            "after": {
                "session_id": "s2",
                "distance_mi": 4.0,
            },
            "revision": {
                "revision_id": "r1",
                "scope": "day",
            },
        }

        revision = create_plan_revision(
            session=db,
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            revision_type="modify_day",
            status="applied",
            deltas=deltas,
        )
        db.commit()

        # Verify deltas were stored
        assert revision.deltas is not None
        assert revision.deltas["before"]["distance_mi"] == 5.0
        assert revision.deltas["after"]["distance_mi"] == 4.0
        assert revision.deltas["revision"]["scope"] == "day"
