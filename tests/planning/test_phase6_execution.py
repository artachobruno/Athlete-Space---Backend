"""Phase 6 Execution Tests.

Tests for safe calendar writes, conflict detection, and compliance tracking.
All tests use transactional database operations (no mocks for DB logic).
"""

from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.calendar.conflicts import CalendarConflict, ConflictType, detect_execution_conflicts_batch
from app.calendar.write_service import CalendarWriteService, WriteResult
from app.db.models import AthleteProfile, AuthProvider, PlannedSession, StravaAccount, User
from app.db.session import get_session
from app.metrics.compliance import SessionCompliance, get_session_compliance, record_completion, record_manual_edit, record_skip
from app.planning.execution.contracts import ExecutableSession
from app.planning.execution.execute_plan import ExecutionResult, execute_week_plan
from app.planning.execution.guards import (
    ExecutionGuardError,
    InvalidDurationError,
    InvalidSessionSourceError,
    MissingPlanIdError,
    MissingSessionTemplateIdError,
    validate_executable_session,
)
from app.planning.output.models import MaterializedSession, WeekPlan


@pytest.fixture
def test_user_id() -> str:
    """Create a test user and return user_id."""
    user_id = str(uuid4())
    athlete_id = 99999

    with get_session() as session:
        # Create user
        user = User(
            id=user_id,
            email=f"test_{user_id}@example.com",
            password_hash=None,
            auth_provider=AuthProvider.password,
            strava_athlete_id=athlete_id,
            created_at=datetime.now(UTC),
            last_login_at=None,
        )
        session.add(user)

        # Create AthleteProfile
        profile = AthleteProfile(
            user_id=user_id,
            athlete_id=athlete_id,
            sources={},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(profile)

        # Create StravaAccount (backup lookup)
        account = StravaAccount(
            user_id=user_id,
            athlete_id=str(athlete_id),
            access_token="test_token",
            refresh_token="test_refresh",
            expires_at=2147483647,  # Max PostgreSQL integer (Jan 19, 2038)
            last_sync_at=None,
            oldest_synced_at=None,
            full_history_synced=False,
            sync_success_count=0,
            sync_failure_count=0,
            last_sync_error=None,
            created_at=datetime.now(UTC),
        )
        session.add(account)
        session.commit()

    yield user_id

    # Cleanup
    with get_session() as session:
        session.execute(select(PlannedSession).where(PlannedSession.user_id == user_id)).delete()
        session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).delete()
        session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).delete()
        session.execute(select(User).where(User.id == user_id)).delete()
        session.commit()


@pytest.fixture
def test_plan_id() -> str:
    """Test plan ID."""
    return "test-plan-123"


@pytest.fixture
def test_start_date() -> date:
    """Test plan start date (Monday)."""
    return date(2024, 1, 1)  # Monday


def create_executable_session(
    session_id: str,
    plan_id: str,
    week_index: int,
    session_date: date,
    duration_minutes: int = 45,
    distance_miles: float = 5.0,
    session_type: str = "easy",
    session_template_id: str = "template_1",
) -> ExecutableSession:
    """Create a test ExecutableSession."""
    return ExecutableSession(
        session_id=session_id,
        plan_id=plan_id,
        week_index=week_index,
        date=session_date,
        duration_minutes=duration_minutes,
        distance_miles=distance_miles,
        session_type=session_type,
        session_template_id=session_template_id,
        source="ai_plan",
    )


def create_week_plan(week_index: int, sessions: list[MaterializedSession]) -> WeekPlan:
    """Create a test WeekPlan."""
    total_duration = sum(s.duration_minutes for s in sessions)
    total_distance = sum(s.distance_miles for s in sessions)
    return WeekPlan(
        week_index=week_index,
        sessions=sessions,
        total_duration_min=total_duration,
        total_distance_miles=round(total_distance, 2),
    )


def test_validate_executable_session_success():
    """Test validation passes for valid ExecutableSession."""
    session = create_executable_session(
        session_id="test-1",
        plan_id="plan-1",
        week_index=0,
        session_date=date(2024, 1, 1),
    )
    validate_executable_session(session)  # Should not raise


def test_validate_executable_session_missing_plan_id():
    """Test validation fails for missing plan_id."""
    session = ExecutableSession(
        session_id="test-1",
        plan_id="",  # Empty plan_id
        week_index=0,
        date=date(2024, 1, 1),
        duration_minutes=45,
        distance_miles=5.0,
        session_type="easy",
        session_template_id="template_1",
        source="ai_plan",
    )
    with pytest.raises(MissingPlanIdError):
        validate_executable_session(session)


def test_validate_executable_session_missing_template_id():
    """Test validation fails for missing session_template_id."""
    session = ExecutableSession(
        session_id="test-1",
        plan_id="plan-1",
        week_index=0,
        date=date(2024, 1, 1),
        duration_minutes=45,
        distance_miles=5.0,
        session_type="easy",
        session_template_id="",  # Empty template_id
        source="ai_plan",
    )
    with pytest.raises(MissingSessionTemplateIdError):
        validate_executable_session(session)


def test_validate_executable_session_invalid_source():
    """Test validation fails for invalid source."""
    session = ExecutableSession(
        session_id="test-1",
        plan_id="plan-1",
        week_index=0,
        date=date(2024, 1, 1),
        duration_minutes=45,
        distance_miles=5.0,
        session_type="easy",
        session_template_id="template_1",
        source="manual",  # Invalid source
    )
    with pytest.raises(InvalidSessionSourceError):
        validate_executable_session(session)


def test_validate_executable_session_invalid_duration():
    """Test validation fails for invalid duration."""
    session = ExecutableSession(
        session_id="test-1",
        plan_id="plan-1",
        week_index=0,
        date=date(2024, 1, 1),
        duration_minutes=0,  # Invalid duration
        distance_miles=5.0,
        session_type="easy",
        session_template_id="template_1",
        source="ai_plan",
    )
    with pytest.raises(InvalidDurationError):
        validate_executable_session(session)


def test_idempotent_double_write(test_user_id: str, test_plan_id: str):
    """Test that idempotent double-write is safe."""
    service = CalendarWriteService()
    session_date = date(2024, 1, 1)
    session_id = str(uuid4())

    executable = create_executable_session(
        session_id=session_id,
        plan_id=test_plan_id,
        week_index=0,
        session_date=session_date,
    )

    # First write
    result1 = service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=False,
    )

    assert result1.sessions_written == 1
    assert len(result1.conflicts_detected) == 0

    # Second write (idempotent)
    result2 = service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=False,
    )

    assert result2.sessions_written == 0  # Already exists
    assert len(result2.conflicts_detected) == 0

    # Verify only one session in database
    with get_session() as session:
        count = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == test_user_id,
            )
        ).scalar_one_or_none()
        assert count is not None  # Should exist
        assert count.id == session_id


def test_dry_run_detects_conflicts(test_user_id: str, test_plan_id: str):
    """Test that dry-run detects conflicts without writing."""
    service = CalendarWriteService()

    # Create existing session
    existing_session_id = str(uuid4())
    session_date = date(2024, 1, 1)

    with get_session() as session:
        athlete_id = session.execute(
            select(AthleteProfile).where(AthleteProfile.user_id == test_user_id)
        ).first()[0].athlete_id

        existing = PlannedSession(
            id=existing_session_id,
            user_id=test_user_id,
            athlete_id=athlete_id,
            date=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title="Existing Run",
            duration_minutes=60,
            distance_km=10.0,
            plan_type="season",
            plan_id=test_plan_id,
            week_number=1,
            status="planned",
            completed=False,
        )
        session.add(existing)
        session.commit()

    # Try to write overlapping session
    new_session_id = str(uuid4())
    executable = create_executable_session(
        session_id=new_session_id,
        plan_id=test_plan_id,
        week_index=0,
        session_date=session_date,  # Same date
    )

    # Dry run
    result = service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.sessions_written == 1  # Dry run counts as "would write"
    assert len(result.conflicts_detected) > 0  # Should detect conflict

    # Verify session was NOT actually written
    with get_session() as session:
        new_session = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == new_session_id,
                PlannedSession.user_id == test_user_id,
            )
        ).first()
        assert new_session is None  # Should not exist


def test_duplicate_session_id_blocked(test_user_id: str, test_plan_id: str):
    """Test that duplicate session_id is blocked."""
    service = CalendarWriteService()
    session_id = str(uuid4())
    session_date = date(2024, 1, 1)

    executable = create_executable_session(
        session_id=session_id,
        plan_id=test_plan_id,
        week_index=0,
        session_date=session_date,
    )

    # First write
    result1 = service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=False,
    )
    assert result1.sessions_written == 1

    # Try to write same session_id again (should be idempotent, but test conflict detection)
    # Actually, idempotency means it won't conflict - it will just skip
    # But if we use a different date, it should detect duplicate ID conflict
    executable2 = create_executable_session(
        session_id=session_id,  # Same ID
        plan_id=test_plan_id,
        week_index=0,
        session_date=date(2024, 1, 2),  # Different date
    )

    # This should be idempotent (skip), not conflict
    result2 = service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable2],
        dry_run=False,
    )
    # Idempotent write - already exists, so skips
    assert result2.sessions_written == 0


def test_execute_week_plan_success(test_user_id: str, test_plan_id: str, test_start_date: date):
    """Test successful week plan execution."""
    sessions = [
        MaterializedSession(
            day="mon",
            session_template_id="template_1",
            session_type="easy",
            duration_minutes=45,
            distance_miles=5.0,
        ),
        MaterializedSession(
            day="wed",
            session_template_id="template_2",
            session_type="tempo",
            duration_minutes=30,
            distance_miles=4.0,
        ),
    ]

    week_plan = create_week_plan(week_index=0, sessions=sessions)

    result = execute_week_plan(
        user_id=test_user_id,
        plan_id=test_plan_id,
        week_plan=week_plan,
        start_date=test_start_date,
        allow_conflicts=False,
    )

    assert result.status == "SUCCESS"
    assert result.sessions_written == 2
    assert len(result.conflicts_detected) == 0


def test_execute_week_plan_with_conflicts(test_user_id: str, test_plan_id: str, test_start_date: date):
    """Test week plan execution blocked by conflicts."""
    # Create existing session on Monday
    monday_date = test_start_date  # Monday

    with get_session() as session:
        athlete_id = session.execute(
            select(AthleteProfile).where(AthleteProfile.user_id == test_user_id)
        ).first()[0].athlete_id

        existing = PlannedSession(
            id=str(uuid4()),
            user_id=test_user_id,
            athlete_id=athlete_id,
            date=datetime.combine(monday_date, datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title="Existing Run",
            duration_minutes=60,
            distance_km=10.0,
            plan_type="season",
            plan_id="other-plan",
            week_number=1,
            status="planned",
            completed=False,
        )
        session.add(existing)
        session.commit()

    # Try to execute week plan with session on same Monday
    sessions = [
        MaterializedSession(
            day="mon",
            session_template_id="template_1",
            session_type="easy",
            duration_minutes=45,
            distance_miles=5.0,
        ),
    ]

    week_plan = create_week_plan(week_index=0, sessions=sessions)

    result = execute_week_plan(
        user_id=test_user_id,
        plan_id=test_plan_id,
        week_plan=week_plan,
        start_date=test_start_date,
        allow_conflicts=False,
    )

    assert result.status == "BLOCKED"
    assert result.sessions_written == 0
    assert len(result.conflicts_detected) > 0


def test_compliance_scheduled(test_user_id: str, test_plan_id: str):
    """Test compliance states - scheduled."""
    service = CalendarWriteService()
    session_id = str(uuid4())
    executable = create_executable_session(
        session_id=session_id,
        plan_id=test_plan_id,
        week_index=0,
        session_date=date(2024, 1, 1),
    )

    service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=False,
    )

    compliance = get_session_compliance(session_id, test_user_id)
    assert compliance is not None
    assert compliance.status == "scheduled"


def test_compliance_completed(test_user_id: str, test_plan_id: str):
    """Test compliance states - completed."""
    service = CalendarWriteService()
    session_id = str(uuid4())
    executable = create_executable_session(
        session_id=session_id,
        plan_id=test_plan_id,
        week_index=0,
        session_date=date(2024, 1, 1),
    )

    service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=False,
    )

    record_completion(session_id, test_user_id, completed_duration_min=50)

    compliance = get_session_compliance(session_id, test_user_id)
    assert compliance is not None
    assert compliance.status == "completed"
    assert compliance.completed_duration_min == 50


def test_compliance_skipped(test_user_id: str, test_plan_id: str):
    """Test compliance states - skipped."""
    service = CalendarWriteService()
    session_id = str(uuid4())
    executable = create_executable_session(
        session_id=session_id,
        plan_id=test_plan_id,
        week_index=0,
        session_date=date(2024, 1, 1),
    )

    service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=False,
    )

    record_skip(session_id, test_user_id)

    compliance = get_session_compliance(session_id, test_user_id)
    assert compliance is not None
    assert compliance.status == "skipped"


def test_manual_edit_marks_modified(test_user_id: str, test_plan_id: str):
    """Test that manual edit marks session as modified."""
    service = CalendarWriteService()
    session_id = str(uuid4())
    executable = create_executable_session(
        session_id=session_id,
        plan_id=test_plan_id,
        week_index=0,
        session_date=date(2024, 1, 1),
    )

    service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable],
        dry_run=False,
    )

    record_manual_edit(session_id, test_user_id)

    compliance = get_session_compliance(session_id, test_user_id)
    assert compliance is not None
    assert compliance.status == "modified"


def test_overlapping_sessions_blocked(test_user_id: str, test_plan_id: str):
    """Test that overlapping sessions are blocked."""
    service = CalendarWriteService()
    session_date = date(2024, 1, 1)

    # Create first session
    session_id1 = str(uuid4())
    executable1 = create_executable_session(
        session_id=session_id1,
        plan_id=test_plan_id,
        week_index=0,
        session_date=session_date,
    )

    result1 = service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable1],
        dry_run=False,
    )
    assert result1.sessions_written == 1

    # Try to create overlapping session on same date
    session_id2 = str(uuid4())
    executable2 = create_executable_session(
        session_id=session_id2,
        plan_id=test_plan_id,
        week_index=0,
        session_date=session_date,  # Same date
    )

    result2 = service.write_week(
        user_id=test_user_id,
        plan_id=test_plan_id,
        sessions=[executable2],
        dry_run=False,
    )

    assert result2.sessions_written == 0
    assert len(result2.conflicts_detected) > 0
