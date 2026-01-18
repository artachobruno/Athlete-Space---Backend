"""Input builder for Style LLM.

Transforms OrchestratorAgentResponse and executor reply into StyleLLMInput.
"""

import re

from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.responses.prompts import StyleLLMInput


def _extract_single_metric(text: str) -> str | None:
    """Extract a single metric from text, prioritizing TSB/CTL/ATL.

    Args:
        text: Executor reply text

    Returns:
        Single metric description or None
    """
    # Look for TSB first (most important signal)
    tsb_match = re.search(r"TSB[:\s]+(-?\d+\.?\d*)", text, re.IGNORECASE)
    if tsb_match:
        value = tsb_match.group(1)
        # Check for context (falling, rising, stable)
        trend_match = re.search(r"load trend[:\s]+(\w+)", text, re.IGNORECASE)
        trend = trend_match.group(1) if trend_match else None
        if trend:
            return f"training stress balance (TSB) of {value} with a {trend} load trend"
        return f"training stress balance (TSB) of {value}"

    # Look for CTL
    ctl_match = re.search(r"CTL[:\s]+(-?\d+\.?\d*)", text, re.IGNORECASE)
    if ctl_match:
        value = ctl_match.group(1)
        return f"chronic training load (CTL) of {value}"

    # Look for ATL
    atl_match = re.search(r"ATL[:\s]+(-?\d+\.?\d*)", text, re.IGNORECASE)
    if atl_match:
        value = atl_match.group(1)
        return f"acute training load (ATL) of {value}"

    return None


def _extract_goal_from_decision(
    decision: OrchestratorAgentResponse, _athlete_state: AthleteState | None
) -> str:
    """Extract training goal from decision.

    Args:
        decision: Orchestrator decision
        _athlete_state: Optional athlete state for context (unused, reserved for future use)

    Returns:
        Goal description
    """
    # Check structured_data for race/plan info
    structured = decision.structured_data or {}

    # Look for race information
    if decision.horizon == "race":
        race_distance = structured.get("race_distance")
        if race_distance:
            return f"{race_distance.lower()} race build"

    # Look for season plan
    if decision.horizon == "season":
        return "season build"

    # Look for week plan
    if decision.horizon == "week":
        return "weekly training"

    # Default based on intent
    if decision.intent == "plan":
        return "training plan"
    if decision.intent == "explain":
        return "training state"

    return "training"


def _extract_headline(decision: OrchestratorAgentResponse) -> str:
    """Extract headline from decision.

    Args:
        decision: Orchestrator decision

    Returns:
        Headline text
    """
    # Use response_type to generate headline
    if decision.response_type == "summary":
        return "Your training is on track"
    if decision.response_type == "explanation":
        return "Here's what I see"
    if decision.response_type == "recommendation":
        return "Here's my recommendation"
    if decision.response_type == "plan":
        return "Your plan is ready"
    if decision.response_type == "weekly_plan":
        return "Your week is planned"

    return "Your training status"


def _extract_situation(
    decision: OrchestratorAgentResponse, athlete_state: AthleteState | None, executor_reply: str
) -> str:
    """Extract situation from decision and athlete state.

    Args:
        decision: Orchestrator decision (currently unused, reserved for future use)
        athlete_state: Optional athlete state
        executor_reply: Executor reply text

    Returns:
        Situation description
    """
    # decision parameter kept for API consistency, may be used in future
    _ = decision
    # Try to extract from executor reply first
    # Look for context clues
    if "well-recovered" in executor_reply.lower():
        return "You're well-recovered and ready for quality work"
    if "fatigue" in executor_reply.lower() or "fatigued" in executor_reply.lower():
        return "You're carrying some fatigue"
    if "recovery" in executor_reply.lower() and "good" in executor_reply.lower():
        return "You're in a good recovery state"

    # Use athlete state if available
    if athlete_state:
        if athlete_state.tsb > 10:
            return "You're well-recovered and ready for quality work"
        if athlete_state.tsb < -10:
            return "You're carrying some fatigue"
        if athlete_state.load_trend == "falling":
            return "Your load is decreasing, which suggests good recovery"
        if athlete_state.load_trend == "stable":
            return "Your load is holding steady"
        if athlete_state.load_trend == "rising":
            return "Your load is building steadily"

    # Default
    return "You're mid-block with manageable fatigue"


def _extract_action(decision: OrchestratorAgentResponse, executor_reply: str) -> str:
    """Extract action from decision and reply.

    Args:
        decision: Orchestrator decision
        executor_reply: Executor reply text

    Returns:
        Action description
    """
    # Check if action is "no change"
    if "no change" in executor_reply.lower() or "stay the course" in executor_reply.lower():
        return "No changes recommended"

    # Check for adjustment language
    if "reduce" in executor_reply.lower() or "decrease" in executor_reply.lower():
        return "Reduce training load"
    if "increase" in executor_reply.lower() or "add" in executor_reply.lower():
        return "Increase training load"

    # Default based on intent
    if decision.intent == "explain":
        return "No changes recommended"

    return "Continue with current plan"


def _extract_next_cta(decision: OrchestratorAgentResponse, executor_reply: str) -> str | None:
    """Extract next call-to-action from decision and reply.

    Args:
        decision: Orchestrator decision
        executor_reply: Executor reply text

    Returns:
        CTA text or None
    """
    # Look for explicit CTA in reply
    if "reassess" in executor_reply.lower():
        # Extract time reference
        if "after" in executor_reply.lower():
            # Try to extract what comes after
            after_match = re.search(r"after\s+([^\.]+)", executor_reply.lower())
            if after_match:
                return f"Let's reassess after {after_match.group(1)}"
        return "Let's reassess after your next session"

    # Look for follow_up
    if decision.follow_up:
        return decision.follow_up

    # Default based on horizon
    if decision.horizon == "week":
        return "Let's reassess after your long run"
    if decision.horizon == "race":
        return "Let's check in next week"

    return None


def build_style_input(
    decision: OrchestratorAgentResponse,
    executor_reply: str,
    athlete_state: AthleteState | None = None,
) -> StyleLLMInput:
    """Build StyleLLMInput from orchestrator decision and executor reply.

    Args:
        decision: Orchestrator decision
        executor_reply: Executor reply text
        athlete_state: Optional athlete state for context

    Returns:
        Structured input for Style LLM
    """
    # Extract components
    goal = _extract_goal_from_decision(decision, athlete_state)
    headline = _extract_headline(decision)
    situation = _extract_situation(decision, athlete_state, executor_reply)  # decision parameter kept for API consistency
    signal = _extract_single_metric(executor_reply) or "stable training load"
    action = _extract_action(decision, executor_reply)
    next_cta = _extract_next_cta(decision, executor_reply)

    # Ensure CTA exists - default to reassurance if None
    if not next_cta:
        next_cta = "All good for now."

    return StyleLLMInput(
        goal=goal,
        headline=headline,
        situation=situation,
        signal=signal,
        action=action,
        next=next_cta,
    )
