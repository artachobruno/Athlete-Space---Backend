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


def _extract_headline(_decision: OrchestratorAgentResponse) -> str | None:
    """Extract headline from decision.

    Args:
        _decision: Orchestrator decision (unused, kept for API compatibility)

    Returns:
        Headline text or None to let LLM generate it
    """
    # Let the Style LLM generate headlines organically based on context
    # Do not provide hardcoded templates - all responses must be LLM-generated
    return None


def _extract_situation(
    decision: OrchestratorAgentResponse, athlete_state: AthleteState | None, executor_reply: str
) -> str:
    """Extract situation from decision and athlete state.

    Args:
        decision: Orchestrator decision (currently unused, reserved for future use)
        athlete_state: Optional athlete state
        executor_reply: Executor reply text

    Returns:
        Situation description - passes raw context to LLM for generation
    """
    # Instead of hardcoded templates, pass structured context to the Style LLM
    # The LLM will generate the situation description organically
    # decision parameter kept for API consistency, may be used in future
    _ = decision

    # Build context from executor reply and athlete state for LLM to interpret
    context_parts = []

    # Add athlete state context if available
    if athlete_state:
        if athlete_state.tsb is not None:
            context_parts.append(f"Training Stress Balance (TSB): {athlete_state.tsb:.1f}")
        if athlete_state.load_trend:
            context_parts.append(f"Load trend: {athlete_state.load_trend}")
        if athlete_state.volatility:
            context_parts.append(f"Volatility: {athlete_state.volatility}")

    # Add executor reply context (truncated for brevity)
    if executor_reply:
        # Pass a condensed version of the executor reply as context
        context_parts.append(f"Context from analysis: {executor_reply[:200]}")

    # Return structured context for LLM to interpret, not a pre-written template
    if context_parts:
        return " | ".join(context_parts)

    # Minimal fallback - LLM should still generate based on this
    return "Current training state analysis"


def _extract_action(decision: OrchestratorAgentResponse, executor_reply: str) -> str:
    """Extract action from decision and reply.

    Args:
        decision: Orchestrator decision
        executor_reply: Executor reply text

    Returns:
        Action description - passes raw context to LLM for generation
    """
    # Instead of hardcoded templates, pass structured context to the Style LLM
    # The LLM will generate the action description organically

    # Build action context from executor reply for LLM to interpret
    action_context = []

    # Pass relevant parts of executor reply as context
    if executor_reply:
        # Look for key action indicators but don't template them
        if "no change" in executor_reply.lower() or "stay the course" in executor_reply.lower():
            action_context.append("Recommendation: maintain current approach")
        elif "reduce" in executor_reply.lower() or "decrease" in executor_reply.lower():
            action_context.append("Recommendation: reduce training load")
        elif "increase" in executor_reply.lower() or "add" in executor_reply.lower():
            action_context.append("Recommendation: increase training load")
        else:
            # Pass decision intent as context
            action_context.append(f"Intent: {decision.intent}")

    # Return structured context for LLM to interpret, not a pre-written template
    if action_context:
        return " | ".join(action_context)

    # Minimal fallback based on intent - LLM should still generate based on this
    if decision.intent == "explain":
        return "Status: explaining current state"

    return "Status: reviewing training plan"


def _extract_next_cta(decision: OrchestratorAgentResponse, executor_reply: str) -> str | None:
    """Extract next call-to-action from decision and reply.

    Args:
        decision: Orchestrator decision
        executor_reply: Executor reply text

    Returns:
        CTA context or None - passes raw context to LLM for generation
    """
    # Instead of hardcoded templates, pass structured context to the Style LLM
    # The LLM will generate the CTA organically

    # Build CTA context from executor reply and decision for LLM to interpret
    cta_context = []

    # Look for explicit CTA indicators in executor reply but don't template them
    if "reassess" in executor_reply.lower():
        if "after" in executor_reply.lower():
            # Extract time reference as context
            after_match = re.search(r"after\s+([^\.]+)", executor_reply.lower())
            if after_match:
                cta_context.append(f"Reassess timing: after {after_match.group(1)}")
            else:
                cta_context.append("Reassess timing: after next session")
        else:
            cta_context.append("Reassess timing: after next session")

    # Pass follow_up from decision as context, not as template
    if decision.follow_up:
        cta_context.append(f"Suggested follow-up: {decision.follow_up}")

    # Pass horizon as context
    if decision.horizon == "week":
        cta_context.append("Horizon: week")
    elif decision.horizon == "race":
        cta_context.append("Horizon: race")

    # Return structured context for LLM to interpret, not a pre-written template
    if cta_context:
        return " | ".join(cta_context)

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
