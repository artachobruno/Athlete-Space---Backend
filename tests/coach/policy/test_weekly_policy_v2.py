from app.coach.policy.intent_context import IntentContext
from app.coach.policy.weekly_policy_v0 import WeeklyDecision
from app.coach.policy.weekly_policy_v2 import decide_weekly_action_v2
from app.tools.semantic.evaluate_plan_change import PlanStateSummary


def make_state(
    *,
    planned_total_week: int = 6,
    planned_elapsed: int = 3,
    planned_remaining: int = 3,
    executed_elapsed: int = 3,
    compliance_rate: float = 1.0,
    phase: str | None = None,
    days_to_race: int | None = None,
    injury_status: str | None = None,
    subjective_fatigue: str | None = None,
    atl: float | None = None,
    ctl: float | None = None,
) -> PlanStateSummary:
    """Create a safe state for testing (no safety triggers by default)."""
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


def test_weak_intent_no_change():
    """Test Rule 2: Weak intent suppression → NO_CHANGE."""
    state = make_state()
    intent = IntentContext(
        request_source="athlete_reflective",
        intent_strength="weak",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "exploratory" in result.reason.lower() or "does not justify" in result.reason.lower()


def test_reflective_intent_proposes_adjustment():
    """Test Rule 3: Reflective athlete intent → PROPOSE_ADJUSTMENT."""
    state = make_state()
    intent = IntentContext(
        request_source="athlete_reflective",
        intent_strength="moderate",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "reflection" in result.reason.lower() or "proposing adjustment" in result.reason.lower()


def test_strong_explicit_intent_proposes_plan():
    """Test Rule 4: Strong explicit athlete intent → PROPOSE_PLAN."""
    state = make_state()
    intent = IntentContext(
        request_source="athlete_explicit",
        intent_strength="strong",
        execution_requested=True,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert "explicit" in result.reason.lower() or "athlete request" in result.reason.lower()


def test_system_detected_proposes_adjustment():
    """Test Rule 5: System detected issues → PROPOSE_ADJUSTMENT."""
    state = make_state()
    intent = IntentContext(
        request_source="system_detected",
        intent_strength="moderate",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "system" in result.reason.lower() or "detected" in result.reason.lower()


def test_safety_rule_taper_overrides_intent():
    """Test Rule 1: Safety rule (taper) overrides intent."""
    state = make_state(
        phase="taper",
        days_to_race=10,  # Within 14 days
    )
    intent = IntentContext(
        request_source="athlete_explicit",
        intent_strength="strong",
        execution_requested=True,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Taper freeze should override strong explicit intent
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "taper" in result.reason.lower() or "race day" in result.reason.lower()


def test_safety_rule_injury_overrides_intent():
    """Test Rule 1: Safety rule (injury) overrides intent."""
    state = make_state(
        injury_status="injured",
    )
    intent = IntentContext(
        request_source="athlete_explicit",
        intent_strength="strong",
        execution_requested=True,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Injury safety gate should override strong explicit intent
    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "injury" in result.reason.lower()


def test_safety_rule_fatigue_overrides_intent():
    """Test Rule 1: Safety rule (fatigue) overrides intent."""
    state = make_state(
        subjective_fatigue="high",
    )
    intent = IntentContext(
        request_source="athlete_reflective",
        intent_strength="moderate",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Fatigue override should take precedence
    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "fatigue" in result.reason.lower()


def test_fallback_delegates_to_v1():
    """Test Rule 6: Fallback delegates to v1 when no v2 rules match."""
    # State with no plan (v1 Rule 6 will return PROPOSE_PLAN)
    state = make_state(
        planned_total_week=0,
        planned_elapsed=0,
        planned_remaining=0,
        executed_elapsed=0,
        compliance_rate=1.0,
    )
    intent = IntentContext(
        request_source="coach_suggested",
        intent_strength="moderate",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Should fall back to v1, which returns PROPOSE_PLAN for no plan
    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert "no training plan" in result.reason.lower()


def test_fallback_to_v1_default_behavior():
    """Test Rule 6: Fallback to v1 default 'on track' behavior."""
    # Safe state with no safety triggers and no matching v2 rules
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
    )
    intent = IntentContext(
        request_source="coach_suggested",
        intent_strength="moderate",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Should fall back to v1 default: NO_CHANGE with "on track" reason
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "on track" in result.reason.lower() or "no changes needed" in result.reason.lower()


def test_early_week_stability_overrides_intent():
    """Test Rule 1: Early week stability overrides intent."""
    state = make_state(
        planned_total_week=10,
        planned_elapsed=2,  # 20% elapsed (< 30%)
        planned_remaining=8,
        executed_elapsed=2,
        compliance_rate=1.0,
    )
    intent = IntentContext(
        request_source="athlete_explicit",
        intent_strength="strong",
        execution_requested=True,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Early week stability should override strong intent
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "just starting" in result.reason.lower() or "monitoring" in result.reason.lower()


def test_compliance_trigger_overrides_intent():
    """Test Rule 1: Low compliance overrides intent."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=4,
        planned_remaining=2,
        executed_elapsed=2,
        compliance_rate=0.5,  # < 0.7
    )
    intent = IntentContext(
        request_source="athlete_reflective",
        intent_strength="weak",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Compliance trigger should override weak intent
    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "compliance" in result.reason.lower()


def test_coach_suggested_falls_back_to_v1():
    """Test that coach_suggested without matching rules falls back to v1."""
    state = make_state()
    intent = IntentContext(
        request_source="coach_suggested",
        intent_strength="moderate",
        execution_requested=False,
    )

    result = decide_weekly_action_v2(state=state, intent=intent)

    # Should fall back to v1 (on track behavior)
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "on track" in result.reason.lower() or "no changes needed" in result.reason.lower()
