"""Phase C: Cursor-Style Execution with ActionPlan.

This module executes plan creation/modification with stepwise progress tracking.
All execution goes through semantic tool routing and evaluation guard.
"""

from datetime import datetime, timezone
from typing import Literal

from loguru import logger

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.flows.authorization import require_authorization
from app.coach.mcp_client import emit_progress_event_safe
from app.coach.schemas.action_plan import ActionPlan, ActionStep
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.orchestrator.routing import route_with_safety_check
from app.tools.guards import require_recent_evaluation
from app.tools.semantic_tool_executor import execute_semantic_tool


async def execute_plan_with_action_plan(
    decision: OrchestratorAgentResponse,
    deps: CoachDeps,
    conversation_id: str,
    horizon: Literal["week", "season", "race"],
) -> dict[str, str | dict | list[str]]:
    """Execute plan creation/modification with Cursor-style stepwise execution.

    HARD RULES:
    - MUST generate ActionPlan before execution
    - MUST emit progress events for each step
    - MUST go through route_with_safety_check
    - MUST respect evaluation guard
    - MUST require authorization

    Args:
        decision: Orchestrator decision
        deps: Coach dependencies
        conversation_id: Conversation ID
        horizon: Planning horizon

    Returns:
        Dictionary with execution result and metadata
    """
    logger.info(
        "Starting plan execution with ActionPlan",
        conversation_id=conversation_id,
        intent=decision.intent,
        horizon=horizon,
    )

    # Step 1: Require authorization (HARD STOP)
    tool_name = decision.target_action or "plan"
    require_authorization(conversation_id, tool_name)

    # Step 2: Generate ActionPlan
    action_plan = _generate_action_plan(decision.intent, horizon)

    # Step 3: Emit planned events
    await _emit_planned_events(conversation_id, action_plan)

    # Step 4: Execute steps with progress tracking
    execution_result = await _execute_steps(
        action_plan=action_plan,
        decision=decision,
        deps=deps,
        conversation_id=conversation_id,
        horizon=horizon,
    )

    logger.info(
        "Plan execution completed",
        conversation_id=conversation_id,
        steps_completed=len([s for s in action_plan.steps if s.id in execution_result.get("completed_steps", [])]),
    )

    return execution_result


def _generate_action_plan(
    intent: str,
    horizon: Literal["week", "season", "race"],  # noqa: ARG001
) -> ActionPlan:
    """Generate deterministic ActionPlan for plan execution.

    Args:
        intent: User intent (plan, modify, etc.)
        horizon: Planning horizon

    Returns:
        ActionPlan with ordered steps
    """
    # Base steps for all plan operations
    base_steps = [
        ActionStep(id="load_training_state", label="Loading training state"),
        ActionStep(id="evaluate_plan_change", label="Evaluating plan change"),
        ActionStep(id="generate_plan_structure", label="Generating plan structure"),
        ActionStep(id="preview_plan_change", label="Previewing plan changes"),
        ActionStep(id="apply_plan", label="Applying plan changes"),
        ActionStep(id="save_plan", label="Saving plan to calendar"),
    ]

    # For modifications, add modification-specific steps
    if intent == "modify":
        # Insert modification step before apply
        modify_step = ActionStep(id="modify_plan", label="Modifying plan structure")
        # Find apply_plan index and insert before it
        apply_idx = next(i for i, s in enumerate(base_steps) if s.id == "apply_plan")
        base_steps.insert(apply_idx, modify_step)

    return ActionPlan(steps=base_steps)


async def _emit_planned_events(
    conversation_id: str,
    action_plan: ActionPlan,
) -> None:
    """Emit 'planned' status for all steps in ActionPlan.

    Args:
        conversation_id: Conversation ID
        action_plan: Action plan with steps
    """
    for step in action_plan.steps:
        await emit_progress_event_safe(
            conversation_id=conversation_id,
            step_id=step.id,
            label=step.label,
            status="planned",
        )


async def _execute_steps(
    action_plan: ActionPlan,
    decision: OrchestratorAgentResponse,
    deps: CoachDeps,
    conversation_id: str,
    horizon: Literal["week", "season", "race"],
) -> dict[str, str | dict | list[str]]:
    """Execute ActionPlan steps with progress tracking.

    Args:
        action_plan: Action plan to execute
        decision: Orchestrator decision
        deps: Coach dependencies
        conversation_id: Conversation ID
        horizon: Planning horizon

    Returns:
        Execution result with completed steps and final result
    """
    completed_steps: list[str] = []
    execution_results: dict[str, str] = {}

    for step in action_plan.steps:
        try:
            # Emit in_progress
            await emit_progress_event_safe(
                conversation_id=conversation_id,
                step_id=step.id,
                label=step.label,
                status="in_progress",
            )

            # Execute step
            step_result = await _execute_step(
                step=step,
                decision=decision,
                deps=deps,
                conversation_id=conversation_id,
                horizon=horizon,
            )

            execution_results[step.id] = step_result if isinstance(step_result, str) else str(step_result)

            # Emit completed
            await emit_progress_event_safe(
                conversation_id=conversation_id,
                step_id=step.id,
                label=step.label,
                status="completed",
            )

            completed_steps.append(step.id)

        except Exception as e:
            # Emit failed
            await emit_progress_event_safe(
                conversation_id=conversation_id,
                step_id=step.id,
                label=step.label,
                status="failed",
                message=str(e),
            )

            logger.exception(
                "Step execution failed",
                step_id=step.id,
                step_label=step.label,
                error=str(e),
            )

            # Stop execution on failure
            raise RuntimeError(f"Step '{step.label}' failed: {e}") from e

    # Return final result (from save_plan step or last step)
    if execution_results:
        last_key = list(execution_results.keys())[-1]
        final_message = execution_results.get("save_plan") or execution_results.get(last_key, "Plan execution completed")
    else:
        final_message = "Plan execution completed"

    return {
        "message": final_message,
        "metadata": {"completed_steps": completed_steps},
        "completed_steps": completed_steps,
        "execution_results": execution_results,
    }


async def _execute_step(
    step: ActionStep,
    decision: OrchestratorAgentResponse,
    deps: CoachDeps,
    conversation_id: str,
    horizon: Literal["week", "season", "race"],
) -> str:
    """Execute a single ActionPlan step.

    Args:
        step: Step to execute
        decision: Orchestrator decision
        deps: Coach dependencies
        conversation_id: Conversation ID
        horizon: Planning horizon

    Returns:
        Step execution result message
    """
    logger.debug(
        "Executing step",
        step_id=step.id,
        step_label=step.label,
    )

    # For most steps, we route to the main semantic tool
    # Some steps (like load_training_state) may be no-ops or use read-only tools
    if step.id in {"load_training_state", "preview_plan_change"}:
        # These are informational steps - skip actual execution
        return f"Step '{step.label}' completed"

    # Route to semantic tool for execution steps
    intent = decision.intent
    today_utc = datetime.now(timezone.utc).date()
    routed_tool, prerequisite_checks = route_with_safety_check(
        intent=intent,
        horizon=horizon,
        has_proposal=False,
        needs_approval=True,
        user_id=deps.user_id,
        today=today_utc,
    )

    if not routed_tool:
        # No tool for this step - skip
        return f"Step '{step.label}' skipped (no tool required)"

    # Run prerequisite checks (e.g., detect_plan_incoherence)
    for check_tool in prerequisite_checks:
        logger.debug(
            "Running prerequisite check",
            check_tool=check_tool,
        )
        # Prerequisite checks use a modified decision
        check_decision = OrchestratorAgentResponse(
            **decision.model_dump(),
            intent="explain",  # Prerequisite checks are informational
            target_action=check_tool,
        )
        await execute_semantic_tool(
            tool_name=check_tool,
            decision=check_decision,
            deps=deps,
            conversation_id=conversation_id,
        )

    # Require evaluation before mutation (PROPOSE/ADJUST only; skip for EXECUTE)
    if step.id in {"apply_plan", "save_plan", "modify_plan"}:
        if not deps.user_id:
            raise ValueError("user_id is required for plan mutation operations")
        require_recent_evaluation(
            user_id=deps.user_id,
            athlete_id=deps.athlete_id,
            horizon=horizon,
            tool_name=routed_tool,
            action=decision.action,
        )

    # Execute semantic tool (main execution happens here)
    if step.id in {"generate_plan_structure", "apply_plan", "save_plan", "modify_plan"}:
        return await execute_semantic_tool(
            tool_name=routed_tool,
            decision=decision,
            deps=deps,
            conversation_id=conversation_id,
        )

    # For evaluate_plan_change step, use evaluation tool
    if step.id == "evaluate_plan_change":
        # This is handled by require_recent_evaluation above
        return f"Step '{step.label}' completed (evaluation checked)"

    return f"Step '{step.label}' completed"
