"""Behavioral validation: 5 golden scenarios.

This test suite observes what the coach actually does in 5 key scenarios
before writing any policy logic. The goal is to understand current behavior,
not to enforce correctness.

These tests are NON-BLOCKING observational probes. They should not fail builds.

Run with: pytest tests/coach/test_golden_scenarios_behavioral.py -v -s
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.behavioral

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models import Activity, PlannedSession, StravaAccount, User
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change

# ============================================================================
# Scenario Fixtures (Read-only test data)
# ============================================================================


def _create_test_user(db_session, user_id: str, athlete_id: int) -> str:
    """Helper to create a test user."""
    user = User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash=None,
        auth_provider="password",
        created_at=datetime.now(UTC),
    )
    db_session.add(user)

    # Create StravaAccount for athlete_id mapping
    account = StravaAccount(
        user_id=user_id,
        athlete_id=str(athlete_id),
        access_token="test_token",
        refresh_token="test_refresh",
        expires_at=2147483647,
        created_at=datetime.now(UTC),
    )
    db_session.add(account)
    db_session.commit()
    return user_id


@pytest.fixture
def today() -> date:
    """Today's date for testing."""
    return date(2026, 1, 23)  # Thursday


# Scenario 1: Healthy Base
# - Plan exists with consistent execution
# - High compliance (>90%)
# - No risk flags
# - Regular activity pattern
@pytest.fixture
def scenario_healthy_base(db_session, today: date) -> dict[str, Any]:
    """Scenario 1: Healthy base training."""
    user_id = _create_test_user(db_session, "test-user-healthy-base", 1)
    # Create planned sessions for next 2 weeks (high compliance expected)
    planned_sessions = []
    for day_offset in range(14):
        session_date = today + timedelta(days=day_offset)
        if session_date.weekday() in [0, 2, 4, 6]:  # Mon, Wed, Fri, Sun
            session = PlannedSession(
                user_id=user_id,
                title=f"Easy Run {day_offset}",
                sport="run",
                duration_seconds=3600,  # 1 hour
                distance_meters=8000,  # ~5 miles
                intensity="easy",
                starts_at=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
                status="completed" if day_offset < 7 else "planned",
            )
            planned_sessions.append(session)

    # Create completed activities (high compliance)
    activities = []
    for day_offset in range(-7, 0):  # Last 7 days
        activity_date = today + timedelta(days=day_offset)
        if activity_date.weekday() in [0, 2, 4, 6]:
            activity = Activity(
                user_id=user_id,
                sport="run",
                title=f"Run {day_offset}",
                distance_meters=8000,
                duration_seconds=3600,
                starts_at=datetime.combine(activity_date, datetime.min.time()).replace(tzinfo=UTC),
                source="strava",
                source_activity_id=f"strava-{day_offset}",
            )
            activities.append(activity)

    db_session.add_all(planned_sessions)
    db_session.add_all(activities)
    db_session.commit()

    return {
        "name": "Healthy Base",
        "description": "Consistent execution, high compliance, no issues",
        "user_id": user_id,
        "planned_sessions": planned_sessions,
        "activities": activities,
    }


# Scenario 2: Missed Long Run
# - Plan has long run scheduled
# - Long run was missed (not completed)
# - Other sessions completed
# - Compliance drops but not critically low
@pytest.fixture
def scenario_missed_long_run(db_session, today: date) -> dict[str, Any]:
    """Scenario 2: Missed long run."""
    user_id = _create_test_user(db_session, "test-user-missed-long-run", 2)
    planned_sessions = []
    activities = []

    # Create planned sessions for next 2 weeks
    for day_offset in range(14):
        session_date = today + timedelta(days=day_offset)
        if session_date.weekday() in [0, 2, 4, 6]:
            is_long_run = session_date.weekday() == 6 and day_offset < 7  # Sunday long run in past week
            duration = 7200 if is_long_run else 3600  # 2 hours for long run
            distance = 16000 if is_long_run else 8000

            session = PlannedSession(
                user_id=user_id,
                title="Long Run" if is_long_run else f"Easy Run {day_offset}",
                sport="run",
                duration_seconds=duration,
                distance_meters=distance,
                intensity="easy",
                starts_at=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
                status="completed" if day_offset < 7 and not is_long_run else "planned",
            )
            planned_sessions.append(session)

    # Create completed activities (missing the long run)
    for day_offset in range(-7, 0):
        activity_date = today + timedelta(days=day_offset)
        if activity_date.weekday() in [0, 2, 4]:  # Mon, Wed, Fri - but NOT Sunday
            activity = Activity(
                user_id=user_id,
                sport="run",
                title=f"Run {day_offset}",
                distance_meters=8000,
                duration_seconds=3600,
                starts_at=datetime.combine(activity_date, datetime.min.time()).replace(tzinfo=UTC),
                source="strava",
                source_activity_id=f"strava-{day_offset}",
            )
            activities.append(activity)

    db_session.add_all(planned_sessions)
    db_session.add_all(activities)
    db_session.commit()

    return {
        "name": "Missed Long Run",
        "description": "Long run was missed, other sessions completed",
        "user_id": user_id,
        "planned_sessions": planned_sessions,
        "activities": activities,
    }


# Scenario 3: Fatigue
# - Recent activities show signs of fatigue
# - Lower volume than planned
# - Some sessions skipped
# - Compliance drops below 70%
@pytest.fixture
def scenario_fatigue(db_session, today: date) -> dict[str, Any]:
    """Scenario 3: Fatigue indicators."""
    user_id = _create_test_user(db_session, "test-user-fatigue", 3)
    planned_sessions = []
    activities = []

    # Create planned sessions (ambitious plan)
    for day_offset in range(14):
        session_date = today + timedelta(days=day_offset)
        if session_date.weekday() in [0, 1, 2, 4, 5, 6]:  # 6 days/week
            session = PlannedSession(
                user_id=user_id,
                title=f"Run {day_offset}",
                sport="run",
                duration_seconds=3600,
                distance_meters=8000,
                intensity="easy",
                starts_at=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
                status="completed" if day_offset < 7 else "planned",
            )
            planned_sessions.append(session)

    # Create fewer completed activities (fatigue = skipping sessions)
    for day_offset in range(-7, 0):
        activity_date = today + timedelta(days=day_offset)
        if activity_date.weekday() in [0, 4]:  # Only 2 out of 6 planned sessions
            activity = Activity(
                user_id=user_id,
                sport="run",
                title=f"Run {day_offset}",
                distance_meters=6000,  # Shorter than planned (fatigue)
                duration_seconds=2400,  # Shorter duration
                starts_at=datetime.combine(activity_date, datetime.min.time()).replace(tzinfo=UTC),
                source="strava",
                source_activity_id=f"strava-fatigue-{day_offset}",
            )
            activities.append(activity)

    db_session.add_all(planned_sessions)
    db_session.add_all(activities)
    db_session.commit()

    return {
        "name": "Fatigue",
        "description": "Low compliance, reduced volume, skipped sessions",
        "user_id": user_id,
        "planned_sessions": planned_sessions,
        "activities": activities,
    }


# Scenario 4: Taper
# - Plan is in taper phase (reduced volume)
# - High compliance with taper sessions
# - Approaching race date
# - Lower volume is intentional
@pytest.fixture
def scenario_taper(db_session, today: date) -> dict[str, Any]:
    """Scenario 4: Taper phase."""
    user_id = _create_test_user(db_session, "test-user-taper", 4)
    planned_sessions = []
    activities = []

    # Create taper sessions (reduced volume, 3-4 days/week)
    for day_offset in range(14):
        session_date = today + timedelta(days=day_offset)
        if session_date.weekday() in [1, 3, 5]:  # Tue, Thu, Sat (3 days/week)
            duration = 1800 if day_offset < 7 else 2400  # Shorter sessions
            distance = 4000 if day_offset < 7 else 5000

            session = PlannedSession(
                user_id=user_id,
                title=f"Taper Run {day_offset}",
                sport="run",
                duration_seconds=duration,
                distance_meters=distance,
                intensity="easy",
                starts_at=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
                status="completed" if day_offset < 7 else "planned",
            )
            planned_sessions.append(session)

    # Create completed activities matching taper plan
    for day_offset in range(-7, 0):
        activity_date = today + timedelta(days=day_offset)
        if activity_date.weekday() in [1, 3, 5]:
            activity = Activity(
                user_id=user_id,
                sport="run",
                title=f"Taper Run {day_offset}",
                distance_meters=4000,
                duration_seconds=1800,
                starts_at=datetime.combine(activity_date, datetime.min.time()).replace(tzinfo=UTC),
                source="strava",
                source_activity_id=f"strava-taper-{day_offset}",
            )
            activities.append(activity)

    db_session.add_all(planned_sessions)
    db_session.add_all(activities)
    db_session.commit()

    return {
        "name": "Taper",
        "description": "Taper phase - reduced volume is intentional",
        "user_id": user_id,
        "planned_sessions": planned_sessions,
        "activities": activities,
    }


# Scenario 5: No Race
# - Plan exists but no race anchor
# - Maintenance mode
# - Moderate compliance
# - No specific goal
@pytest.fixture
def scenario_no_race(db_session, today: date) -> dict[str, Any]:
    """Scenario 5: No race goal."""
    user_id = _create_test_user(db_session, "test-user-no-race", 5)
    planned_sessions = []
    activities = []

    # Create maintenance plan (moderate volume, 4 days/week)
    for day_offset in range(14):
        session_date = today + timedelta(days=day_offset)
        if session_date.weekday() in [0, 2, 4, 6]:  # 4 days/week
            session = PlannedSession(
                user_id=user_id,
                title=f"Maintenance Run {day_offset}",
                sport="run",
                duration_seconds=3600,
                distance_meters=8000,
                intensity="easy",
                starts_at=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
                status="completed" if day_offset < 7 else "planned",
            )
            planned_sessions.append(session)

    # Create completed activities (moderate compliance ~75%)
    for day_offset in range(-7, 0):
        activity_date = today + timedelta(days=day_offset)
        if activity_date.weekday() in [0, 2, 4]:  # 3 out of 4 planned
            activity = Activity(
                user_id=user_id,
                sport="run",
                title=f"Run {day_offset}",
                distance_meters=8000,
                duration_seconds=3600,
                starts_at=datetime.combine(activity_date, datetime.min.time()).replace(tzinfo=UTC),
                source="strava",
                source_activity_id=f"strava-{day_offset}",
            )
            activities.append(activity)

    db_session.add_all(planned_sessions)
    db_session.add_all(activities)
    db_session.commit()

    return {
        "name": "No Race",
        "description": "Maintenance mode, no race goal, moderate compliance",
        "user_id": user_id,
        "planned_sessions": planned_sessions,
        "activities": activities,
    }


# ============================================================================
# Behavioral Observation Tests
# ============================================================================


def test_scenario_healthy_base(
    scenario_healthy_base: dict[str, Any],
    today: date,
):
    """Observe coach behavior in healthy base scenario."""
    result = evaluate_plan_change(
        user_id=scenario_healthy_base["user_id"],
        athlete_id=1,
        horizon="week",
        today=today,
    )

    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {scenario_healthy_base['name']}")
    print(f"{'=' * 60}")
    print(f"Decision: {result.decision.decision}")
    print(f"Reasons: {result.decision.reasons}")
    print(f"Confidence: {result.decision.confidence:.2f}")
    print(f"State Summary: {result.current_state_summary}")
    print(result.current_state)


def test_scenario_missed_long_run(
    scenario_missed_long_run: dict[str, Any],
    today: date,
):
    """Observe coach behavior when long run is missed."""
    result = evaluate_plan_change(
        user_id=scenario_missed_long_run["user_id"],
        athlete_id=2,
        horizon="week",
        today=today,
    )

    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {scenario_missed_long_run['name']}")
    print(f"{'=' * 60}")
    print(f"Decision: {result.decision.decision}")
    print(f"Reasons: {result.decision.reasons}")
    print(f"Confidence: {result.decision.confidence:.2f}")
    print(f"State Summary: {result.current_state_summary}")
    print(result.current_state)


def test_scenario_fatigue(
    scenario_fatigue: dict[str, Any],
    today: date,
):
    """Observe coach behavior when fatigue is detected."""
    result = evaluate_plan_change(
        user_id=scenario_fatigue["user_id"],
        athlete_id=3,
        horizon="week",
        today=today,
    )

    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {scenario_fatigue['name']}")
    print(f"{'=' * 60}")
    print(f"Decision: {result.decision.decision}")
    print(f"Reasons: {result.decision.reasons}")
    print(f"Confidence: {result.decision.confidence:.2f}")
    print(f"State Summary: {result.current_state_summary}")
    print(result.current_state)


def test_scenario_taper(
    scenario_taper: dict[str, Any],
    today: date,
):
    """Observe coach behavior during taper phase."""
    result = evaluate_plan_change(
        user_id=scenario_taper["user_id"],
        athlete_id=4,
        horizon="week",
        today=today,
    )

    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {scenario_taper['name']}")
    print(f"{'=' * 60}")
    print(f"Decision: {result.decision.decision}")
    print(f"Reasons: {result.decision.reasons}")
    print(f"Confidence: {result.decision.confidence:.2f}")
    print(f"State Summary: {result.current_state_summary}")
    print(result.current_state)


def test_scenario_no_race(
    scenario_no_race: dict[str, Any],
    today: date,
):
    """Observe coach behavior with no race goal."""
    result = evaluate_plan_change(
        user_id=scenario_no_race["user_id"],
        athlete_id=5,
        horizon="week",
        today=today,
    )

    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {scenario_no_race['name']}")
    print(f"{'=' * 60}")
    print(f"Decision: {result.decision.decision}")
    print(f"Reasons: {result.decision.reasons}")
    print(f"Confidence: {result.decision.confidence:.2f}")
    print(f"State Summary: {result.current_state_summary}")
    print(result.current_state)


def test_all_scenarios_summary(
    scenario_healthy_base: dict[str, Any],
    scenario_missed_long_run: dict[str, Any],
    scenario_fatigue: dict[str, Any],
    scenario_taper: dict[str, Any],
    scenario_no_race: dict[str, Any],
    today: date,
):
    """Run all scenarios and print summary table.

    TEMPORARY: This test is for policy design phase only.
    Once policy v0 is defined, delete this test and convert
    individual scenario tests to policy enforcement tests.
    """
    scenarios = [
        (scenario_healthy_base, 1),
        (scenario_missed_long_run, 2),
        (scenario_fatigue, 3),
        (scenario_taper, 4),
        (scenario_no_race, 5),
    ]

    results = []
    for scenario, athlete_id in scenarios:
        result = evaluate_plan_change(
            user_id=scenario["user_id"],
            athlete_id=athlete_id,
            horizon="week",
            today=today,
        )
        results.append({
            "scenario": scenario["name"],
            "decision": result.decision.decision,
            "reasons": result.decision.reasons,
            "confidence": result.decision.confidence,
        })

    # Print outcome table
    print(f"\n{'=' * 80}")
    print("OUTCOME TABLE: What the coach does today")
    print(f"{'=' * 80}")
    print(f"{'Scenario':<20} | {'Decision':<25} | {'Confidence':<10} | {'OK?'}")
    print("-" * 80)

    for r in results:
        # Determine OK status (simplified - will need policy to determine)
        decision = r["decision"]
        ok_status = "❓"  # Unknown - needs policy to determine

        print(f"{r['scenario']:<20} | {decision:<25} | {r['confidence']:<10.2f} | {ok_status}")

    print(f"\n{'=' * 80}")
    print("NOTE: This is behavioral observation, not policy validation.")
    print("Use this data to inform policy v0.")
    print(f"{'=' * 80}\n")
