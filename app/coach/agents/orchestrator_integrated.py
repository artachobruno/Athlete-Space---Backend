"""Integrated orchestrator with classification, guard, and unified planning.

This orchestrator implements the complete flow:
User → Classifier → Guard → Tool → Response

Every message is classified before action.
Only one intentional action happens per turn.
State mutations are explicit and logged.
"""

from datetime import datetime, timezone

from loguru import logger

from app.coach.admin.decision_logger import DECISION_LOGGER
from app.coach.admin.execution_guard import EXECUTION_GUARD
from app.coach.agents.orchestrator_classifier import classify_intent
from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.schemas.orchestration import OrchestrationDecision
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.tools.unified_plan import plan_tool


async def run_orchestrated_conversation(
    user_input: str,
    deps: CoachDeps,
) -> OrchestratorAgentResponse:
    """Execute orchestrated conversation with classification and guard.

    Flow:
    1. Classify user intent
    2. Check execution guard
    3. Execute tool if allowed
    4. Log decision
    5. Return response

    Args:
        user_input: User's message
        deps: Coach dependencies

    Returns:
        OrchestratorAgentResponse
    """
    logger.info(
        "Starting orchestrated conversation",
        user_input_preview=user_input[:100],
        athlete_id=deps.athlete_id,
    )

    # Reset execution guard for new session
    EXECUTION_GUARD.reset_session()

    # Build minimal context for classifier
    minimal_context = _build_minimal_context(deps)

    # Step 1: Classify intent
    decision = await classify_intent(user_input, deps, minimal_context)

    # Step 2: Check execution guard
    allowed, guard_reason = EXECUTION_GUARD.check(decision)

    tool_executed = False
    tool_name = None
    response_message = ""

    if not allowed:
        # Guard blocked - downgrade to NO_TOOL
        logger.warning(
            "Execution guard blocked tool call",
            tool_name=decision.tool_name,
            reason=guard_reason,
        )
        decision = EXECUTION_GUARD.downgrade_to_no_tool(decision, guard_reason or "unknown")

    # Step 3: Execute tool if action=CALL_TOOL
    if decision.action == "CALL_TOOL" and allowed:
        tool_name = decision.tool_name
        try:
            # Execute unified plan tool
            if tool_name == "plan":
                response_message = await _execute_plan_tool(decision, user_input, deps, minimal_context)
                tool_executed = True
                EXECUTION_GUARD.record_call(tool_name)
            else:
                # Unknown tool - should not happen if guard is working
                logger.error(f"Unknown tool requested: {tool_name}")
                response_message = "I'm not able to execute that action right now. Could you rephrase your request?"
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            response_message = "I encountered an error while processing your request. Could you try again?"
    else:
        # NO_TOOL - generate conversational response
        response_message = _generate_conversational_response(user_input, decision, deps)

    # Step 4: Log decision
    DECISION_LOGGER.log(
        user_id=deps.user_id,
        athlete_id=deps.athlete_id,
        user_input=user_input,
        decision=decision,
        tool_executed=tool_executed,
        tool_name=tool_name,
        guard_blocked=not allowed,
        guard_reason=guard_reason,
    )

    # Step 5: Return response
    return OrchestratorAgentResponse(
        message=response_message,
        intent=decision.user_intent,
        response_type="tool" if tool_executed else "conversation",
        structured_data={"decision": decision.model_dump()},
        follow_up=None,
    )


def _build_minimal_context(deps: CoachDeps) -> dict:
    """Build minimal context for classifier.

    Args:
        deps: Coach dependencies

    Returns:
        Minimal context dictionary
    """
    context: dict = {
        "today_date": datetime.now(timezone.utc).date().isoformat(),
    }

    # Check if plan exists (simplified - in production would query database)
    # For now, we'll assume no plan exists
    context["last_plan_exists"] = False

    # Add recent activity summary if available
    if deps.athlete_state:
        context["recent_activity"] = (
            f"CTL: {deps.athlete_state.ctl:.1f}, TSB: {deps.athlete_state.tsb:.1f}, Trend: {deps.athlete_state.load_trend}"
        )

    return context


async def _execute_plan_tool(
    decision: OrchestrationDecision,
    user_input: str,
    deps: CoachDeps,
    minimal_context: dict,  # noqa: ARG001
) -> str:
    """Execute the unified plan tool.

    Args:
        decision: Orchestration decision
        user_input: Original user message
        deps: Coach dependencies
        minimal_context: Minimal context

    Returns:
        Response message from tool
    """
    # Get current plan if this is a revision
    current_plan = None
    if decision.user_intent == "revise":
        # In production, would fetch existing plan from database
        # For now, we'll pass None (new plan)
        current_plan = None

    # Build activity state summary
    activity_state = None
    if deps.athlete_state:
        activity_state = {
            "ctl": deps.athlete_state.ctl,
            "atl": deps.athlete_state.atl,
            "tsb": deps.athlete_state.tsb,
            "load_trend": deps.athlete_state.load_trend,
            "confidence": deps.athlete_state.confidence,
        }

    # Execute plan tool
    return await plan_tool(
        horizon=decision.horizon,
        user_feedback=user_input,
        current_plan=current_plan,
        activity_state=activity_state,
        user_id=deps.user_id,
        athlete_id=deps.athlete_id,
    )


def _generate_conversational_response(
    user_input: str,  # noqa: ARG001
    decision: OrchestrationDecision,
    deps: CoachDeps,
) -> str:
    """Generate conversational response when no tool is executed.

    Args:
        user_input: User's message
        decision: Orchestration decision
        deps: Coach dependencies

    Returns:
        Conversational response
    """
    # Simple conversational responses based on intent
    if decision.user_intent == "explain":
        return "I can help explain your training state, plans, or concepts. What would you like me to explain?"
    if decision.user_intent == "assess":
        if deps.athlete_state:
            return (
                f"Your current training state: CTL {deps.athlete_state.ctl:.1f}, "
                f"TSB {deps.athlete_state.tsb:.1f}. "
                f"Load trend: {deps.athlete_state.load_trend}."
            )
        return "I'd need more training data to provide a proper assessment."
    if decision.user_intent == "question":
        return "I'm here to help with your training questions. Could you provide more details about what you'd like to know?"

    # Default response
    return "I understand. How can I help you with your training?"
