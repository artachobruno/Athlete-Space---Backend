"""Semantic tool to executor method mapping.

This module maps semantic tool names to their executor implementations.
All tool execution must go through routing → semantic tool → this mapping → executor.
"""

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse


async def execute_semantic_tool(
    tool_name: str,
    decision: OrchestratorAgentResponse,
    deps: CoachDeps,
    conversation_id: str | None = None,
) -> str:
    """Execute a semantic tool by routing to appropriate executor method.

    Args:
        tool_name: Semantic tool name (from routing)
        decision: Orchestrator decision
        deps: Dependencies
        conversation_id: Optional conversation ID

    Returns:
        Execution result message

    Raises:
        ValueError: If tool_name is not a recognized semantic tool
    """
    # Lazy import to avoid circular dependency
    from app.coach.executor.action_executor import CoachActionExecutor

    horizon = decision.horizon

    # Map semantic tools to executor methods based on intent/horizon
    # This is the ONLY place where semantic tools map to implementations

    if tool_name == "plan":
        if horizon == "race":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("plan_race_build")
            return await CoachActionExecutor._execute_plan_race(decision, deps, conversation_id)
        if horizon == "season":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("plan_season")
            return await CoachActionExecutor._execute_plan_season(decision, deps, conversation_id)
        if horizon == "week":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("plan_week")
            return await CoachActionExecutor._execute_plan_week(decision, deps, conversation_id)
        if horizon == "today":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("plan_single_day")
            return await CoachActionExecutor._execute_plan_day(decision, deps, conversation_id)
        raise ValueError(f"Plan tool requires valid horizon, got: {horizon}")

    if tool_name == "modify":
        if horizon == "today":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("modify_day")
            return await CoachActionExecutor._execute_modify_day(decision, deps, conversation_id)
        if horizon == "week":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("modify_week")
            return await CoachActionExecutor._execute_modify_week(decision, deps, conversation_id)
        if horizon == "race":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("modify_race")
            return await CoachActionExecutor._execute_modify_race(decision, deps, conversation_id)
        if horizon == "season":
            if deps.execution_guard:
                deps.execution_guard.mark_executed("modify_season")
            return await CoachActionExecutor._execute_modify_season(decision, deps, conversation_id)
        raise ValueError(f"Modify tool requires valid horizon, got: {horizon}")

    if tool_name == "recommend_next_session":
        if deps.execution_guard:
            deps.execution_guard.mark_executed("recommend_next_session")
        return await CoachActionExecutor._execute_recommend_next_session(decision, deps, conversation_id)

    if tool_name == "explain_training_state":
        # Handle None horizon by defaulting to week
        if decision.horizon is None or decision.horizon == "none":
            # Create a modified decision with week horizon for tools that require it
            modified_decision = OrchestratorAgentResponse(
                **decision.model_dump(),
                horizon="week",  # Default to week for general explain queries
            )
            decision = modified_decision
        if deps.execution_guard:
            deps.execution_guard.mark_executed("explain_training_state")
        return await CoachActionExecutor._execute_explain_training_state(decision, deps, conversation_id)

    if tool_name == "adjust_training_load":
        if deps.execution_guard:
            deps.execution_guard.mark_executed("adjust_training_load")
        return await CoachActionExecutor._execute_adjust_training_load(decision, deps, conversation_id)

    if tool_name == "add_workout":
        if deps.execution_guard:
            deps.execution_guard.mark_executed("add_workout")
        return await CoachActionExecutor._execute_add_workout(decision, deps, conversation_id)

    if tool_name == "log":
        if deps.execution_guard:
            deps.execution_guard.mark_executed("add_workout")  # log uses add_workout internally
        return await CoachActionExecutor._execute_add_workout(decision, deps, conversation_id)

    if tool_name == "confirm":
        if deps.execution_guard:
            deps.execution_guard.mark_executed("confirm")
        return await CoachActionExecutor._execute_confirm_revision(decision, deps, conversation_id)

    # New semantic tools that don't have executor methods yet
    if tool_name in {"evaluate_plan_change", "preview_plan_change", "detect_plan_incoherence", "explain_plan_structure"}:
        # These are called directly, not through executor
        # They're decision/info tools, not mutations
        raise ValueError(
            f"Semantic tool '{tool_name}' should be called directly, not through executor. "
            "This indicates a routing error."
        )

    raise ValueError(f"Unknown semantic tool: {tool_name}")
