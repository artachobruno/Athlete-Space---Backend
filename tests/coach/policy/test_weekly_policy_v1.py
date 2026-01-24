from app.coach.policy.weekly_policy_v0 import WeeklyDecision
from app.coach.policy.weekly_policy_v1 import decide_weekly_action
from app.tools.semantic.evaluate_plan_change import PlanStateSummary


def make_state(
    *,
    planned_total_week: int,
    planned_elapsed: int,
    planned_remaining: int,
    executed_elapsed: int,
    compliance_rate: float,
    phase: str | None = None,
    days_to_race: int | None = None,
    injury_status: str | None = None,
    subjective_fatigue: str | None = None,
    atl: float | None = None,
    ctl: float | None = None,
):
    return PlanStateSummary(
        planned_total_week=planned_total_week,
        planned_elapsed=planned_elapsed,
        planned_remaining=planned_remaining,
        executed_elapsed=executed_elapsed,
        compliance_rate=compliance_rate,
        summary_text="",
        phase=phase,
        days_to_race=days_to_race,
        injury_status=injury_status,
        subjective_fatigue=subjective_fatigue,
        atl=atl,
        ctl=ctl,
    )


def test_no_change_during_taper():
    """Test Rule 1: Taper freeze - no changes during taper phase close to race."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=2,
        compliance_rate=0.67,
        phase="taper",
        days_to_race=10,  # Within 14 days
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "taper" in result.reason.lower() or "race day" in result.reason.lower()


def test_taper_but_far_from_race():
    """Test that taper phase only triggers if days_to_race <= 14."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=2,
        compliance_rate=0.67,
        phase="taper",
        days_to_race=20,  # Beyond 14 days
    )

    result = decide_weekly_action(state)

    # Should not trigger taper freeze, may trigger other rules
    assert result.decision != WeeklyDecision.NO_CHANGE or "taper" not in result.reason.lower()


def test_injury_forces_adjustment():
    """Test Rule 2: Injury status forces adjustment."""
    # Test with "injured" status
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        injury_status="injured",
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "injury" in result.reason.lower()

    # Test with "managing" status
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        injury_status="managing",
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "injury" in result.reason.lower()


def test_no_injury_allows_normal_flow():
    """Test that injury_status="none" doesn't block normal flow."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        injury_status="none",
    )

    result = decide_weekly_action(state)

    # Should proceed to other rules, not blocked by injury
    assert result.decision != WeeklyDecision.PROPOSE_ADJUSTMENT or "injury" not in result.reason.lower()


def test_high_fatigue_triggers_adjustment():
    """Test Rule 3: High fatigue triggers adjustment."""
    # Test with subjective_fatigue="high"
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        subjective_fatigue="high",
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "fatigue" in result.reason.lower()

    # Test with ATL >> CTL (ATL >= CTL * 1.2)
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        atl=61.0,  # > 20% higher than CTL (50 * 1.2 = 60)
        ctl=50.0,
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "fatigue" in result.reason.lower()


def test_atl_slightly_higher_than_ctl():
    """Test that ATL slightly higher than CTL doesn't trigger (must be >>)."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        atl=55.0,  # Only 10% higher, not 20%
        ctl=50.0,
    )

    result = decide_weekly_action(state)

    # Should not trigger fatigue override
    assert result.decision != WeeklyDecision.PROPOSE_ADJUSTMENT or "fatigue" not in result.reason.lower()


def test_early_week_no_change():
    """Test Rule 4: Early week stability - no changes if < 30% elapsed."""
    state = make_state(
        planned_total_week=10,
        planned_elapsed=2,  # 20% elapsed (< 30%)
        planned_remaining=8,
        executed_elapsed=2,
        compliance_rate=1.0,
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "just starting" in result.reason.lower() or "monitoring" in result.reason.lower()


def test_early_week_with_30_percent_elapsed():
    """Test that 30% elapsed allows other rules to trigger."""
    state = make_state(
        planned_total_week=10,
        planned_elapsed=3,  # 30% elapsed (exactly at threshold)
        planned_remaining=7,
        executed_elapsed=1,
        compliance_rate=0.33,
    )

    result = decide_weekly_action(state)

    # Should proceed to other rules (chronic non-compliance should trigger)
    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT


def test_chronic_non_compliance_adjustment():
    """Test Rule 5: Chronic non-compliance triggers adjustment."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=4,
        planned_remaining=2,
        executed_elapsed=2,
        compliance_rate=0.5,  # < 0.7
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "compliance" in result.reason.lower()


def test_normal_week_defers_to_v0_behavior():
    """Test Rule 6: Normal week defers to v0 behavior."""
    # Test v0 Rule 1: No plan exists
    state = make_state(
        planned_total_week=0,
        planned_elapsed=0,
        planned_remaining=0,
        executed_elapsed=0,
        compliance_rate=1.0,
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert "no training plan" in result.reason.lower()

    # Test v0 Rule 2: Low compliance (< 0.5)
    state = make_state(
        planned_total_week=6,
        planned_elapsed=4,
        planned_remaining=2,
        executed_elapsed=1,
        compliance_rate=0.25,  # < 0.5
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "low compliance" in result.reason.lower() or "sessions completed" in result.reason.lower()

    # Test v0 Rule 3: On track
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "on track" in result.reason.lower() or "no changes needed" in result.reason.lower()


def test_rule_priority_order():
    """Test that rules are evaluated in correct priority order."""
    # Taper should override even with injury
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        phase="taper",
        days_to_race=10,
        injury_status="injured",  # Would trigger Rule 2, but Rule 1 should win
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "taper" in result.reason.lower() or "race day" in result.reason.lower()

    # Injury should override fatigue
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
        injury_status="injured",
        subjective_fatigue="high",  # Would trigger Rule 3, but Rule 2 should win
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "injury" in result.reason.lower()
