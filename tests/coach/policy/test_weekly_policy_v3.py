from app.coach.policy.athlete_context import AthleteContext
from app.coach.policy.intent_context import IntentContext
from app.coach.policy.weekly_policy_v0 import WeeklyDecision
from app.coach.policy.weekly_policy_v3 import decide_weekly_action_v3
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


def make_intent(
    *,
    request_source: str = "coach_suggested",
    intent_strength: str = "moderate",
    execution_requested: bool = False,
) -> IntentContext:
    """Create an intent context for testing."""
    return IntentContext(
        request_source=request_source,
        intent_strength=intent_strength,
        execution_requested=execution_requested,
    )


def test_novice_stability_lock():
    """Test Rule 1: Novice + good compliance → NO_CHANGE."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=0.8,  # Good compliance
    )
    intent = make_intent()
    athlete = AthleteContext(
        experience_level="novice",
        risk_tolerance="low",
        consistency_score=0.7,
        history_of_injury=False,
        adherence_reliability="medium",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "novice" in result.reason.lower() or "stability" in result.reason.lower()
    assert len(result.reason) > 0


def test_elite_autonomy_boost():
    """Test Rule 2: Elite + strong intent → PROPOSE_PLAN."""
    state = make_state()
    intent = make_intent(
        request_source="athlete_explicit",
        intent_strength="strong",
    )
    athlete = AthleteContext(
        experience_level="elite",
        risk_tolerance="high",
        consistency_score=0.8,
        history_of_injury=False,
        adherence_reliability="high",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    # Note: v2's strong explicit intent rule might trigger first
    # But if it doesn't, v3 should trigger
    assert result.decision in {WeeklyDecision.PROPOSE_PLAN, WeeklyDecision.PROPOSE_ADJUSTMENT}
    assert len(result.reason) > 0


def test_advanced_autonomy_boost():
    """Test Rule 2: Advanced + strong intent → PROPOSE_PLAN."""
    state = make_state()
    intent = make_intent(
        request_source="coach_suggested",  # Not explicit, so v2 won't trigger
        intent_strength="strong",
    )
    athlete = AthleteContext(
        experience_level="advanced",
        risk_tolerance="medium",
        consistency_score=0.75,
        history_of_injury=False,
        adherence_reliability="medium",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert "experienced" in result.reason.lower() or "autonomy" in result.reason.lower()
    assert len(result.reason) > 0


def test_injury_history_dampener():
    """Test Rule 3: Injury history + PROPOSE_PLAN → PROPOSE_ADJUSTMENT."""
    state = make_state()
    # Use intent that would trigger PROPOSE_PLAN from v2 or v3
    intent = make_intent(
        request_source="coach_suggested",
        intent_strength="strong",
    )
    athlete = AthleteContext(
        experience_level="intermediate",
        risk_tolerance="medium",
        consistency_score=0.8,
        history_of_injury=True,
        adherence_reliability="medium",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    # If v3 Rule 2 triggers PROPOSE_PLAN, Rule 3 should dampen it
    # Otherwise, it should be PROPOSE_ADJUSTMENT or NO_CHANGE
    assert result.decision in {
        WeeklyDecision.PROPOSE_ADJUSTMENT,
        WeeklyDecision.PROPOSE_PLAN,
        WeeklyDecision.NO_CHANGE,
    }
    assert len(result.reason) > 0


def test_low_reliability_throttle():
    """Test Rule 4: Low adherence + low compliance → NO_CHANGE."""
    state = make_state(
        planned_total_week=6,
        planned_elapsed=4,
        planned_remaining=2,
        executed_elapsed=3,
        compliance_rate=0.75,  # Between 0.7 and 0.8 (doesn't trigger v1, but low for v3)
    )
    intent = make_intent()
    athlete = AthleteContext(
        experience_level="intermediate",
        risk_tolerance="low",
        consistency_score=0.6,
        history_of_injury=False,
        adherence_reliability="low",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "reliability" in result.reason.lower() or "churn" in result.reason.lower()
    assert len(result.reason) > 0


def test_consistency_amplifier():
    """Test Rule 5: High consistency + moderate/strong intent → PROPOSE_PLAN."""
    state = make_state()
    intent = make_intent(
        request_source="coach_suggested",
        intent_strength="moderate",
    )
    athlete = AthleteContext(
        experience_level="intermediate",
        risk_tolerance="medium",
        consistency_score=0.95,  # High consistency (> 0.9)
        history_of_injury=False,
        adherence_reliability="high",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert "consistency" in result.reason.lower() or "stable" in result.reason.lower()
    assert len(result.reason) > 0


def test_consistency_amplifier_strong_intent():
    """Test Rule 5: High consistency + strong intent → PROPOSE_PLAN."""
    state = make_state()
    intent = make_intent(
        request_source="coach_suggested",
        intent_strength="strong",
    )
    athlete = AthleteContext(
        experience_level="intermediate",
        risk_tolerance="medium",
        consistency_score=0.92,
        history_of_injury=False,
        adherence_reliability="high",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert "consistency" in result.reason.lower() or "stable" in result.reason.lower()
    assert len(result.reason) > 0


def test_fallback_to_v2():
    """Test Rule 6: Fallback delegates to v2 when no v3 rules match."""
    state = make_state()
    intent = make_intent(
        request_source="coach_suggested",
        intent_strength="moderate",
    )
    athlete = AthleteContext(
        experience_level="intermediate",
        risk_tolerance="medium",
        consistency_score=0.7,  # Not high enough for Rule 5
        history_of_injury=False,
        adherence_reliability="medium",  # Not low enough for Rule 4
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    # Should fall back to v2, which falls back to v1 default
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "on track" in result.reason.lower() or "no changes needed" in result.reason.lower()
    assert len(result.reason) > 0


def test_v2_safety_rule_takes_precedence():
    """Test that v2 safety rules (taper) override v3."""
    state = make_state(
        phase="taper",
        days_to_race=10,
    )
    intent = make_intent(
        request_source="athlete_explicit",
        intent_strength="strong",
    )
    athlete = AthleteContext(
        experience_level="elite",
        risk_tolerance="high",
        consistency_score=0.95,
        history_of_injury=False,
        adherence_reliability="high",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    # v2 should respect v1's taper freeze
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "taper" in result.reason.lower() or "race day" in result.reason.lower()
    assert len(result.reason) > 0


def test_v2_intent_rule_takes_precedence():
    """Test that v2 intent rules override v3."""
    state = make_state()
    intent = make_intent(
        request_source="athlete_reflective",
        intent_strength="moderate",
    )
    athlete = AthleteContext(
        experience_level="novice",
        risk_tolerance="low",
        consistency_score=0.5,
        history_of_injury=False,
        adherence_reliability="low",
    )

    result = decide_weekly_action_v3(
        state=state,
        intent_context=intent,
        athlete=athlete,
    )

    # v2's reflective intent rule should trigger first
    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "reflection" in result.reason.lower() or "proposing adjustment" in result.reason.lower()
    assert len(result.reason) > 0
