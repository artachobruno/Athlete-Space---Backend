from app.coach.policy.weekly_policy_v0 import WeeklyDecision, WeeklyPolicyResult
from app.coach.policy.weekly_policy_v4 import TrajectoryState, decide_weekly_action_v4, derive_trajectory
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
    plan_changes_last_21_days: int | None = None,
    user_intent_strength: str | None = None,
    experience_level: str | None = None,
) -> PlanStateSummary:
    """Create a state for testing."""
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
        plan_changes_last_21_days=plan_changes_last_21_days,
        user_intent_strength=user_intent_strength,
        experience_level=experience_level,
    )


def make_prior_decision(decision: WeeklyDecision, reason: str = "v3 decision") -> WeeklyPolicyResult:
    """Create a prior decision from v3."""
    return WeeklyPolicyResult(decision=decision, reason=reason)


def test_derive_trajectory_improving():
    """Test trajectory derivation: IMPROVING."""
    state = make_state(
        compliance_rate=0.9,  # ≥ 0.85
        atl=50.0,
        ctl=50.0,  # atl ≤ ctl * 1.1
    )
    trajectory = derive_trajectory(state)
    assert trajectory == TrajectoryState.IMPROVING


def test_derive_trajectory_stable():
    """Test trajectory derivation: STABLE."""
    state = make_state(
        compliance_rate=0.8,  # ≥ 0.75
        atl=60.0,
        ctl=55.0,  # atl ≤ ctl * 1.2
    )
    trajectory = derive_trajectory(state)
    assert trajectory == TrajectoryState.STABLE


def test_derive_trajectory_stagnant():
    """Test trajectory derivation: STAGNANT."""
    state = make_state(
        compliance_rate=0.6,  # < 0.7
        atl=50.0,
        ctl=50.0,  # atl ≈ ctl (within 10%)
    )
    trajectory = derive_trajectory(state)
    assert trajectory == TrajectoryState.STAGNANT


def test_derive_trajectory_declining():
    """Test trajectory derivation: DECLINING."""
    state = make_state(
        compliance_rate=0.6,  # < 0.7
        atl=70.0,
        ctl=50.0,  # atl > ctl * 1.2
    )
    trajectory = derive_trajectory(state)
    assert trajectory == TrajectoryState.DECLINING


def test_derive_trajectory_volatile():
    """Test trajectory derivation: VOLATILE."""
    state = make_state(
        compliance_rate=0.4,  # < 0.5
        atl=50.0,
        ctl=50.0,
    )
    trajectory = derive_trajectory(state)
    assert trajectory == TrajectoryState.VOLATILE


def test_trajectory_lock_improving():
    """Test Rule v4.1: IMPROVING trajectory → NO_CHANGE (even with strong intent)."""
    state = make_state(
        compliance_rate=0.9,
        atl=50.0,
        ctl=50.0,
        days_to_race=30,  # > 21
    )
    prior = make_prior_decision(WeeklyDecision.PROPOSE_PLAN, "v3 wants to propose")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "v4.trajectory_lock" in result.reason
    assert "progressing well" in result.reason.lower() or "momentum" in result.reason.lower()


def test_trajectory_lock_stable():
    """Test Rule v4.1: STABLE trajectory → NO_CHANGE."""
    state = make_state(
        compliance_rate=0.8,
        atl=60.0,
        ctl=55.0,
        days_to_race=30,  # > 21
    )
    prior = make_prior_decision(WeeklyDecision.PROPOSE_ADJUSTMENT, "v3 wants to adjust")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "v4.trajectory_lock" in result.reason


def test_churn_guard():
    """Test Rule v4.2: Churn ≥ 2 → NO_CHANGE."""
    state = make_state(
        compliance_rate=0.8,
        atl=50.0,
        ctl=50.0,
        plan_changes_last_21_days=2,  # ≥ 2
    )
    prior = make_prior_decision(WeeklyDecision.PROPOSE_PLAN, "v3 wants to propose")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "v4.churn_guard" in result.reason
    assert "too many" in result.reason.lower() or "stability" in result.reason.lower()


def test_decline_recovery_early():
    """Test Rule v4.3: DECLINING early (≥28 days) → PROPOSE_ADJUSTMENT."""
    state = make_state(
        compliance_rate=0.6,
        atl=70.0,
        ctl=50.0,  # DECLINING
        days_to_race=35,  # ≥ 28
    )
    prior = make_prior_decision(WeeklyDecision.NO_CHANGE, "v3 says no change")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "v4.decline_recovery" in result.reason
    assert "declining" in result.reason.lower() or "intervention" in result.reason.lower()


def test_decline_recovery_late():
    """Test Rule v4.3: DECLINING late (<28 days) → should not trigger, falls back."""
    state = make_state(
        compliance_rate=0.6,
        atl=70.0,
        ctl=50.0,  # DECLINING
        days_to_race=20,  # < 28
    )
    prior = make_prior_decision(WeeklyDecision.NO_CHANGE, "v3 says no change")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    # Should fall back to v3 since days_to_race < 28
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert result.reason == "v3 says no change"


def test_volatility_dampener():
    """Test Rule v4.4: VOLATILE → PROPOSE_ADJUSTMENT."""
    state = make_state(
        compliance_rate=0.4,  # VOLATILE
        atl=50.0,
        ctl=50.0,
    )
    prior = make_prior_decision(WeeklyDecision.NO_CHANGE, "v3 says no change")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT
    assert "v4.volatility_dampener" in result.reason
    assert "volatile" in result.reason.lower() or "stabilization" in result.reason.lower()


def test_late_stage_freeze():
    """Test Rule v4.5: Late stage (≤14 days) and not declining → NO_CHANGE."""
    state = make_state(
        compliance_rate=0.8,
        atl=50.0,
        ctl=50.0,  # STABLE (not declining)
        days_to_race=10,  # ≤ 14
    )
    prior = make_prior_decision(WeeklyDecision.PROPOSE_PLAN, "v3 wants to propose")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "v4.late_stage_freeze" in result.reason
    assert "race day" in result.reason.lower() or "close" in result.reason.lower()


def test_late_stage_freeze_declining():
    """Test Rule v4.5: Late stage but declining → should allow decline recovery."""
    state = make_state(
        compliance_rate=0.6,
        atl=70.0,
        ctl=50.0,  # DECLINING
        days_to_race=10,  # ≤ 14
    )
    prior = make_prior_decision(WeeklyDecision.NO_CHANGE, "v3 says no change")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    # Decline recovery should not trigger (days_to_race < 28)
    # Late stage freeze should not trigger (trajectory is DECLINING)
    # Should fall back to v3
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert result.reason == "v3 says no change"


def test_strategic_green_light():
    """Test Rule v4.6: STAGNANT + strong intent + experienced → PROPOSE_PLAN."""
    state = make_state(
        compliance_rate=0.6,
        atl=50.0,
        ctl=50.0,  # STAGNANT
        user_intent_strength="strong",
        experience_level="advanced",
    )
    prior = make_prior_decision(WeeklyDecision.NO_CHANGE, "v3 says no change")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert "v4.strategic_green_light" in result.reason
    assert "stagnant" in result.reason.lower() or "strategic" in result.reason.lower()


def test_strategic_green_light_novice():
    """Test Rule v4.6: STAGNANT but novice → should not trigger."""
    state = make_state(
        compliance_rate=0.6,
        atl=50.0,
        ctl=50.0,  # STAGNANT
        user_intent_strength="strong",
        experience_level="beginner",  # Not intermediate/advanced
    )
    prior = make_prior_decision(WeeklyDecision.NO_CHANGE, "v3 says no change")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    # Should fall back to v3
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert result.reason == "v3 says no change"


def test_fallback_missing_data():
    """Test Rule v4.7: Missing data → falls back to v3."""
    state = make_state(
        compliance_rate=0.8,
        atl=None,  # Missing data
        ctl=None,
        days_to_race=None,
    )
    prior = make_prior_decision(WeeklyDecision.PROPOSE_PLAN, "v3 decision")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    # Should fall back to v3 since trajectory can't be determined properly
    assert result.decision == WeeklyDecision.PROPOSE_PLAN
    assert result.reason == "v3 decision"


def test_healthy_compliant_improving_no_change():
    """Sanity check: Healthy, compliant, improving → NO_CHANGE."""
    state = make_state(
        compliance_rate=0.9,  # High compliance
        atl=50.0,
        ctl=50.0,  # IMPROVING
        days_to_race=30,  # Far from race
    )
    prior = make_prior_decision(WeeklyDecision.PROPOSE_PLAN, "v3 wants to propose")

    result = decide_weekly_action_v4(state, prior_decision=prior)

    # Policy v4 should say NO to changes when athlete is healthy, compliant, improving
    assert result.decision == WeeklyDecision.NO_CHANGE
    assert "v4.trajectory_lock" in result.reason
