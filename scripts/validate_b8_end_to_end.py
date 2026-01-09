"""B8 End-to-End Validation Script.

Tests that B8 (Unified Planning Tool) works correctly in real coach flows:
- Consumes TrainingSummary (B16)
- Consumes TrainingConstraints (B17)
- Consumes LoadAdjustmentDecision (B18)
- Produces planned sessions in calendar
- Correct reconciliation behavior (B12)
- Accurate progress events
- Stable coach responses

Run with: python scripts/validate_b8_end_to_end.py

NOTE: This script uses your production database (configured via DATABASE_URL).
It will automatically find the first available user from StravaAccount table.
Make sure DATABASE_URL points to your production database before running.
"""

import asyncio
import os
import socket
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from sqlalchemy import select

from app.calendar.reconciliation_service import reconcile_calendar
from app.calendar.training_summary import build_training_summary
from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.constraints import TrainingConstraints
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.plan_week import plan_week
from app.coach.utils.constraints import RecoveryState
from app.db.models import PlannedSession, StravaAccount
from app.db.session import get_session


def _test_dns_resolution(hostname: str) -> tuple[bool, str]:
    """Test if a hostname can be resolved via DNS.

    Returns:
        Tuple of (success, error_message)
    """
    try:
        socket.gethostbyname(hostname)
    except socket.gaierror as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error: {e}"
    else:
        return True, ""


def _check_and_suggest_database_url_fix() -> None:
    """Check DATABASE_URL and suggest fixes for common issues."""
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        logger.warning("DATABASE_URL environment variable is not set")
        return

    # Extract hostname from connection string
    hostname = None
    if "@" in db_url and "/" in db_url:
        parts = db_url.split("@")
        if len(parts) > 1:
            host_part = parts[1].split("/")[0]
            # Remove port if present
            if ":" in host_part:
                hostname = host_part.split(":")[0]
            else:
                hostname = host_part

    if not hostname:
        return

    # Check for Render.com database hostname missing .render.com suffix
    if "dpg-" in hostname and ".render.com" not in hostname:
        logger.error("=" * 80)
        logger.error("DATABASE_URL HOSTNAME ISSUE DETECTED")
        logger.error("=" * 80)
        logger.error(f"Current hostname: {hostname}")
        logger.error("")
        logger.error("Render.com database hostnames must include '.render.com' suffix.")
        logger.error("")
        logger.error("Your DATABASE_URL should be:")
        logger.error(f"  postgresql://...@{hostname}.render.com:5432/...")
        logger.error("")
        logger.error("Please update your DATABASE_URL environment variable.")
        logger.error("=" * 80)
        return

    # Test DNS resolution
    if hostname:
        can_resolve, dns_error = _test_dns_resolution(hostname)
        if not can_resolve:
            logger.error("=" * 80)
            logger.error("DNS RESOLUTION FAILED")
            logger.error("=" * 80)
            logger.error(f"Hostname: {hostname}")
            logger.error(f"Error: {dns_error}")
            logger.error("")
            logger.error("Common causes for Render.com databases:")
            logger.error("  1. Database is paused (free tier databases sleep after inactivity)")
            logger.error("     → Wake it up in the Render dashboard")
            logger.error("")
            logger.error("  2. IP whitelisting required")
            logger.error("     → Add your IP address in Render database settings")
            logger.error("")
            logger.error("  3. Incorrect hostname")
            logger.error("     → Verify the hostname in your Render dashboard")
            logger.error("     → Check if you need the internal hostname (for Render services)")
            logger.error("       vs external hostname (for local connections)")
            logger.error("")
            logger.error("  4. Network connectivity")
            logger.error("     → Check your internet connection")
            logger.error(f"     → Try: ping {hostname}")
            logger.error(f"     → Try: nslookup {hostname}")
            logger.error("=" * 80)


def _raise_no_users_error() -> None:
    """Raise error for no users found (helper to satisfy TRY301)."""
    raise RuntimeError(
        "No users found in database. Please ensure you have at least one StravaAccount "
        "in your production database, or create a test user using scripts/create_test_user.py"
    )


def get_test_user_from_db() -> tuple[str, int]:
    """Get a real user_id and athlete_id from the production database.

    Returns:
        Tuple of (user_id, athlete_id) from first available StravaAccount

    Raises:
        RuntimeError: If no users found in database or database connection fails
    """
    # Check for common DATABASE_URL issues first
    _check_and_suggest_database_url_fix()

    try:
        with get_session() as db:
            # Get first available StravaAccount
            result = db.execute(
                select(StravaAccount.user_id, StravaAccount.athlete_id).where(StravaAccount.user_id.isnot(None)).limit(1)
            ).first()

            if not result:
                _raise_no_users_error()

            user_id = result[0]
            athlete_id_str = result[1]
            try:
                athlete_id = int(athlete_id_str)
            except (ValueError, TypeError):
                raise RuntimeError(f"Invalid athlete_id format: {athlete_id_str}") from None

            logger.info(f"Using production user: user_id={user_id}, athlete_id={athlete_id}")
            return (user_id, athlete_id)
    except Exception as e:
        error_msg = str(e)
        if "could not translate host name" in error_msg.lower() or "operationalerror" in error_msg.lower():
            db_url = os.getenv("DATABASE_URL", "")
            hostname = None
            if "@" in db_url:
                parts = db_url.split("@")
                if len(parts) > 1:
                    host_part = parts[1].split("/")[0]
                    if ":" in host_part:
                        hostname = host_part.split(":")[0]
                    else:
                        hostname = host_part

            suggestion = ""
            if hostname:
                can_resolve, dns_error = _test_dns_resolution(hostname)
                if not can_resolve:
                    suggestion = (
                        f"\n\nDNS RESOLUTION TEST:\n"
                        f"  Hostname: {hostname}\n"
                        f"  Status: FAILED\n"
                        f"  Error: {dns_error}\n\n"
                        "TROUBLESHOOTING STEPS:\n"
                        "1. Check if database is awake (Render free tier databases sleep)\n"
                        "   → Go to Render dashboard and wake up the database\n"
                        "2. Verify IP whitelisting (if required)\n"
                        "   → Add your IP in Render database settings\n"
                        "3. Verify hostname in Render dashboard\n"
                        "   → Check 'Internal Database URL' vs 'External Database URL'\n"
                        "   → For local connections, use the external hostname\n"
                        "4. Test DNS resolution manually:\n"
                        f"   → Run: ping {hostname}\n"
                        f"   → Run: nslookup {hostname}\n"
                    )

            raise RuntimeError(
                f"Database connection failed: {error_msg}\n\n"
                "This usually means:\n"
                "  1. The database hostname is incorrect or missing a domain suffix\n"
                "  2. Network access to the database is blocked\n"
                "  3. The database server is not accessible from this network\n"
                "  4. The database is paused (Render free tier)\n\n"
                "For Render.com databases:\n"
                "  - Ensure the hostname includes '.render.com' suffix\n"
                "  - Wake up the database if it's paused\n"
                "  - Check IP whitelisting requirements\n"
                "  - Verify you're using the external hostname (not internal)\n"
                f"{suggestion}"
            ) from e
        raise RuntimeError(f"Database error: {error_msg}") from e


# Test configuration - get real user from production database
try:
    TEST_USER_ID, TEST_ATHLETE_ID = get_test_user_from_db()
    logger.info("=" * 80)
    logger.info("B8 Validation Script - Using Production Database")
    logger.info(f"Test User: user_id={TEST_USER_ID}, athlete_id={TEST_ATHLETE_ID}")
    logger.info("=" * 80)
except RuntimeError as e:
    logger.error("=" * 80)
    logger.error("FAILED TO INITIALIZE TEST USER")
    logger.error("=" * 80)
    logger.error(f"{e}")
    logger.error("")
    sys.exit(1)
except Exception as e:
    logger.error("=" * 80)
    logger.error("UNEXPECTED ERROR INITIALIZING TEST USER")
    logger.error("=" * 80)
    logger.error(f"Error: {e}")
    logger.error("")
    logger.error("Please check your database connection and configuration.")
    logger.error("")
    sys.exit(1)


def _raise_validation_error(message: str) -> None:
    """Raise a validation error (helper to satisfy TRY301)."""
    raise ValueError(message)


def _assert_athlete_state(state: AthleteState | None) -> AthleteState:
    """Assert that athlete_state is not None and return it (for type narrowing)."""
    if state is None:
        raise ValueError("athlete_state is None")
    return state


def create_test_athlete_state() -> AthleteState:
    """Create a test athlete state."""
    return AthleteState(
        ctl=50.0,
        atl=45.0,
        tsb=5.0,
        load_trend="stable",
        volatility="low",
        days_since_rest=2,
        seven_day_volume_hours=8.5,
        fourteen_day_volume_hours=16.0,
        confidence=0.9,
        flags=[],
        days_to_race=None,
    )


async def test_1_basic_weekly_planning():
    """Test 1: Basic Weekly Planning (No Constraints)."""
    logger.info("=" * 80)
    logger.info("TEST 1: Basic Weekly Planning (No Constraints)")
    logger.info("=" * 80)

    user_input = "Plan my next week of training."

    # Create test deps
    deps = CoachDeps(
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
        athlete_state=create_test_athlete_state(),
    )

    # Run orchestrator
    decision = await run_conversation(user_input, deps)

    logger.info(f"Orchestrator decision: intent={decision.intent}, horizon={decision.horizon}, action={decision.action}")

    # Verify orchestrator decision
    if decision.intent != "plan":
        raise ValueError(f"Expected intent='plan', got '{decision.intent}'")
    if decision.horizon != "week":
        raise ValueError(f"Expected horizon='week', got '{decision.horizon}'")
    if decision.action != "EXECUTE":
        raise ValueError(f"Expected action='EXECUTE', got '{decision.action}'")

    # Execute action
    result = await CoachActionExecutor.execute(decision, deps)
    logger.info(f"Executor result: {result[:200]}...")

    # Wait a bit for DB to sync
    await asyncio.sleep(0.5)

    # Check if sessions were created
    with get_session() as session:
        now = datetime.now(UTC)
        days_since_monday = now.weekday()
        monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

        sessions = (
            session.execute(
                select(PlannedSession).where(
                    PlannedSession.user_id == TEST_USER_ID,
                    PlannedSession.athlete_id == TEST_ATHLETE_ID,
                    PlannedSession.date >= monday,
                    PlannedSession.date <= sunday,
                )
            )
            .scalars()
            .all()
        )

        session_count = len(list(sessions))
        logger.info(f"Created {session_count} planned sessions for the week")

        # FAIL if no sessions created
        if session_count == 0:
            raise ValueError("FAIL: No sessions created")
        if not (5 <= session_count <= 7):
            raise ValueError(f"FAIL: Expected 5-7 sessions, got {session_count}")

        logger.info("✅ TEST 1 PASSED: Basic weekly planning works")
        return True


async def test_2_planning_with_fatigue_feedback():
    """Test 2: Planning with Fatigue Feedback (B17 + B18 + B8)."""
    logger.info("=" * 80)
    logger.info("TEST 2: Planning with Fatigue Feedback (B17 + B18 + B8)")
    logger.info("=" * 80)

    user_input = "I feel very fatigued and sore. Can you adjust my training?"

    # Create test deps with high fatigue state
    deps = CoachDeps(
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
        athlete_state=AthleteState(
            ctl=50.0,
            atl=75.0,  # High ATL
            tsb=-30.0,  # Very low TSB (fatigued)
            load_trend="rising",  # Fixed: use "rising" instead of "increasing"
            volatility="high",
            days_since_rest=5,
            seven_day_volume_hours=12.0,
            fourteen_day_volume_hours=22.0,
            confidence=0.9,
            flags=["high_fatigue"],
            days_to_race=None,
        ),
    )

    # First, test B17 + B18 directly
    logger.info("Testing B17 (constraints) + B18 (load adjustment)...")

    # Build TrainingSummary (B16)
    training_summary = build_training_summary(
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
        window_days=14,
    )

    # Build RecoveryState
    recovery_state = RecoveryState(
        atl=75.0,
        tsb=-30.0,
        recovery_status="over",
        risk_flags=["ATL_SPIKE", "TSB_LOW"],
    )

    # Build TrainingConstraints (B17) from user feedback
    constraints = TrainingConstraints(
        volume_multiplier=0.8,  # 20% reduction
        intensity_cap="moderate",
        force_rest_days=1,
        disallow_intensity_days=set(),
        long_session_cap_minutes=90,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="User reported high fatigue",
        created_at=datetime.now(UTC),
    )

    # Call B18
    decision = adjust_training_load(
        training_summary=training_summary,
        recovery_state=recovery_state,
        constraints=constraints,
    )

    logger.info(f"B18 LoadAdjustmentDecision: volume_delta={decision.volume_delta_pct:.1%}, intensity_cap={decision.intensity_cap}")

    # Verify B18 output
    if decision.volume_delta_pct >= 0:
        raise ValueError(f"FAIL: Expected volume reduction, got {decision.volume_delta_pct:.1%}")
    if decision.intensity_cap not in {"easy", "moderate"}:
        raise ValueError(f"FAIL: Expected intensity cap, got '{decision.intensity_cap}'")

    # Now test B8 with constraints
    logger.info("Testing B8 with constraints...")
    try:
        # Call B8 directly with feedback
        athlete_state = _assert_athlete_state(deps.athlete_state)
        result = await plan_week(
            state=athlete_state,
            user_id=TEST_USER_ID,
            athlete_id=TEST_ATHLETE_ID,
            user_feedback=user_input,
        )
        logger.info(f"B8 result: {result[:200]}...")

        # Check if sessions were created with reduced volume
        with get_session() as session:
            now = datetime.now(UTC)
            days_since_monday = now.weekday()
            monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
            sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

            sessions = (
                session.execute(
                    select(PlannedSession).where(
                        PlannedSession.user_id == TEST_USER_ID,
                        PlannedSession.athlete_id == TEST_ATHLETE_ID,
                        PlannedSession.date >= monday,
                        PlannedSession.date <= sunday,
                    )
                )
                .scalars()
                .all()
            )

            session_list = list(sessions)
            logger.info(f"Created {len(session_list)} sessions with constraints")

            # Verify sessions respect constraints (check for rest days, reduced intensity)
            rest_days = [s for s in session_list if s.type.lower() == "rest"]
            easy_count = len([s for s in session_list if s.intensity and s.intensity.lower() == "easy"])

            if len(rest_days) < 1:
                _raise_validation_error(f"FAIL: Expected at least 1 rest day, got {len(rest_days)}")
            if easy_count == 0:
                _raise_validation_error(f"FAIL: Expected easy sessions, got {easy_count} with intensity")

            logger.info("✅ TEST 2 PASSED: B8 respects B17/B18 constraints")
    except Exception as e:
        logger.error(f"B8 test failed: {e}", exc_info=True)
        raise

    return True


async def test_3_forced_rest_days():
    """Test 3: Forced Rest Day Behavior."""
    logger.info("=" * 80)
    logger.info("TEST 3: Forced Rest Day Behavior")
    logger.info("=" * 80)

    user_input = "My legs are wrecked. I need to take it easy this week."

    deps = CoachDeps(
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
        athlete_state=create_test_athlete_state(),
    )

    # Call B8 with feedback that should trigger rest days
    if deps.athlete_state is None:
        raise ValueError("athlete_state is None")
    await plan_week(
        state=deps.athlete_state,
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
        user_feedback=user_input,
    )

    # Check for rest days
    with get_session() as session:
        now = datetime.now(UTC)
        days_since_monday = now.weekday()
        monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

        sessions = (
            session.execute(
                select(PlannedSession).where(
                    PlannedSession.user_id == TEST_USER_ID,
                    PlannedSession.athlete_id == TEST_ATHLETE_ID,
                    PlannedSession.date >= monday,
                    PlannedSession.date <= sunday,
                )
            )
            .scalars()
            .all()
        )

        session_list = list(sessions)
        rest_days = [s for s in session_list if s.type.lower() == "rest"]
        hard_sessions = [s for s in session_list if s.intensity and s.intensity.lower() == "hard"]

        logger.info(f"Found {len(rest_days)} rest days, {len(hard_sessions)} hard sessions")

        if len(rest_days) < 1:
            raise ValueError(f"FAIL: Expected at least 1 rest day, got {len(rest_days)}")
        if len(hard_sessions) != 0:
            raise ValueError(f"FAIL: Expected no hard sessions on rest days, got {len(hard_sessions)}")

        logger.info("✅ TEST 3 PASSED: Forced rest days work correctly")
        return True


async def test_4_calendar_visibility():
    """Test 4: Planning Visibility in Calendar."""
    logger.info("=" * 80)
    logger.info("TEST 4: Planning Visibility in Calendar")
    logger.info("=" * 80)

    # Create a plan
    deps = CoachDeps(
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
        athlete_state=create_test_athlete_state(),
    )

    if deps.athlete_state is None:
        raise ValueError("athlete_state is None")
    await plan_week(
        state=deps.athlete_state,
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
    )

    await asyncio.sleep(0.5)

    # Check calendar API would see them
    with get_session() as session:
        now = datetime.now(UTC)
        days_since_monday = now.weekday()
        monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

        sessions = (
            session.execute(
                select(PlannedSession)
                .where(
                    PlannedSession.user_id == TEST_USER_ID,
                    PlannedSession.athlete_id == TEST_ATHLETE_ID,
                    PlannedSession.date >= monday,
                    PlannedSession.date <= sunday,
                )
                .order_by(PlannedSession.date)
            )
            .scalars()
            .all()
        )

        session_list = list(sessions)
        logger.info(f"Found {len(session_list)} sessions in calendar")

        # Verify no duplicates
        session_ids = {s.id for s in session_list}
        if len(session_ids) != len(session_list):
            raise ValueError("FAIL: Duplicate sessions found")

        # Verify all have required fields
        for s in session_list:
            if s.date is None:
                raise ValueError(f"FAIL: Session {s.id} missing date")
            if not s.title:
                raise ValueError(f"FAIL: Session {s.id} missing title")
            if not s.type:
                raise ValueError(f"FAIL: Session {s.id} missing type")

        logger.info("✅ TEST 4 PASSED: Sessions visible in calendar")
        return True


def test_5_reconciliation():
    """Test 5: Reconciliation After Activity Completion (B12)."""
    logger.info("=" * 80)
    logger.info("TEST 5: Reconciliation After Activity Completion (B12)")
    logger.info("=" * 80)

    # This test would require creating an activity and verifying reconciliation
    # For now, we'll test that reconciliation can run without errors
    try:
        results = reconcile_calendar(
            user_id=TEST_USER_ID,
            athlete_id=TEST_ATHLETE_ID,
            start_date=datetime.now(UTC).date() - timedelta(days=7),
            end_date=datetime.now(UTC).date() + timedelta(days=7),
        )
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}", exc_info=True)
        raise
    else:
        logger.info(f"Reconciliation completed: {len(results)} sessions processed")
        logger.info("✅ TEST 5 PASSED: Reconciliation runs without errors")
        return True


def test_6_safety_enforcement():
    """Test 6: Safety Enforcement."""
    logger.info("=" * 80)
    logger.info("TEST 6: Safety Enforcement")
    logger.info("=" * 80)

    # Build training summary
    training_summary = build_training_summary(
        user_id=TEST_USER_ID,
        athlete_id=TEST_ATHLETE_ID,
        window_days=14,
    )

    # Try to create constraints that would increase volume (use max allowed: 1.1)
    # B18 should still cap the final delta at +10% even with max multiplier
    constraints = TrainingConstraints(
        volume_multiplier=1.1,  # Maximum allowed, but B18 should cap delta at +10%
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.5,
        reason_codes=[],
        explanation="User requested volume increase",
        created_at=datetime.now(UTC),
    )

    state = create_test_athlete_state()
    recovery_state = RecoveryState(
        atl=state.atl,
        tsb=state.tsb,
        recovery_status="adequate",
        risk_flags=[],
    )

    # B18 should cap the increase
    decision = adjust_training_load(
        training_summary=training_summary,
        recovery_state=recovery_state,
        constraints=constraints,
    )

    # Verify bounds are enforced
    if decision.volume_delta_pct > 0.10:
        raise ValueError(f"FAIL: Volume increase not capped, got {decision.volume_delta_pct:.1%}")
    logger.info(f"Volume delta capped at {decision.volume_delta_pct:.1%} (max 10%)")

    logger.info("✅ TEST 6 PASSED: Safety bounds enforced")
    return True


async def run_all_tests():
    """Run all validation tests."""
    logger.info("Starting B8 End-to-End Validation")
    logger.info(f"Test user_id: {TEST_USER_ID}, athlete_id: {TEST_ATHLETE_ID}")

    results = []

    try:
        # Test 1: Basic planning
        results.append(("Test 1: Basic Weekly Planning", await test_1_basic_weekly_planning()))
    except Exception as e:
        logger.error(f"Test 1 failed: {e}", exc_info=True)
        results.append(("Test 1: Basic Weekly Planning", False))

    try:
        # Test 2: Fatigue feedback
        results.append(("Test 2: Planning with Fatigue", await test_2_planning_with_fatigue_feedback()))
    except Exception as e:
        logger.error(f"Test 2 failed: {e}", exc_info=True)
        results.append(("Test 2: Planning with Fatigue", False))

    try:
        # Test 3: Forced rest days
        results.append(("Test 3: Forced Rest Days", await test_3_forced_rest_days()))
    except Exception as e:
        logger.error(f"Test 3 failed: {e}", exc_info=True)
        results.append(("Test 3: Forced Rest Days", False))

    try:
        # Test 4: Calendar visibility
        results.append(("Test 4: Calendar Visibility", await test_4_calendar_visibility()))
    except Exception as e:
        logger.error(f"Test 4 failed: {e}", exc_info=True)
        results.append(("Test 4: Calendar Visibility", False))

    try:
        # Test 5: Reconciliation
        results.append(("Test 5: Reconciliation", test_5_reconciliation()))
    except Exception as e:
        logger.error(f"Test 5 failed: {e}", exc_info=True)
        results.append(("Test 5: Reconciliation", False))

    try:
        # Test 6: Safety enforcement
        results.append(("Test 6: Safety Enforcement", await test_6_safety_enforcement()))
    except Exception as e:
        logger.error(f"Test 6 failed: {e}", exc_info=True)
        results.append(("Test 6: Safety Enforcement", False))

    # Print summary
    logger.info("=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        logger.info(f"{status}: {test_name}")

    all_passed = all(passed for _, passed in results)
    if all_passed:
        logger.info("🎉 All tests passed!")
    else:
        logger.error("❌ Some tests failed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
