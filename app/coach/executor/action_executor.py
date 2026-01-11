"""Action Executor for Coach Orchestrator.

Executes coaching actions based on orchestrator decisions.
Owns all MCP tool calls, retries, rate limiting, and safety logic.
"""

import json
from datetime import date, datetime, timezone
from typing import Any, NoReturn

from loguru import logger
from sqlalchemy import select

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.clarification import (
    generate_proactive_clarification,
    generate_slot_clarification,
)
from app.coach.errors import ToolContractViolationError
from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.services.conversation_progress import get_conversation_progress
from app.config.settings import settings
from app.core.conversation_summary import save_conversation_summary, summarize_conversation
from app.core.slot_extraction import generate_clarification_for_missing_slots
from app.core.slot_gate import REQUIRED_SLOTS, validate_slots
from app.db.models import SeasonPlan
from app.db.session import get_session


def serialize_for_mcp(obj: Any) -> Any:
    """Serialize objects for MCP tool calls (JSON-safe conversion).

    Converts date/datetime objects to ISO format strings.
    Recursively handles dicts and lists.

    Args:
        obj: Object to serialize (can be date, datetime, dict, list, or JSON primitive)

    Returns:
        JSON-serializable object (dates/datetimes converted to ISO strings)
    """
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialize_for_mcp(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_for_mcp(v) for v in obj]
    return obj


class CoachActionExecutor:
    """Executes coaching actions based on orchestrator decisions."""

    @staticmethod
    async def _emit_progress_event(
        conversation_id: str,
        step_id: str,
        label: str,
        status: str,
        message: str | None = None,
    ) -> None:
        """Emit a progress event.

        Args:
            conversation_id: Conversation ID
            step_id: Step ID
            label: Step label
            status: Event status
            message: Optional message
        """
        try:
            await call_tool(
                "emit_progress_event",
                {
                    "conversation_id": conversation_id,
                    "step_id": step_id,
                    "label": label,
                    "status": status,
                    "message": message,
                },
            )
            logger.info(
                "Progress event emitted",
                conversation_id=conversation_id,
                step_id=step_id,
                label=label,
                status=status,
                has_message=message is not None,
            )
        except MCPError as e:
            logger.warning(
                f"Failed to emit progress event: {e.code}: {e.message}",
                conversation_id=conversation_id,
                step_id=step_id,
                status=status,
            )

    @staticmethod
    async def _find_step_id_for_tool(
        decision: OrchestratorAgentResponse,
        tool_name: str,
    ) -> tuple[str, str] | None:
        """Find step_id and label for a tool call based on action plan.

        Args:
            decision: Orchestrator decision with action plan
            tool_name: Name of the tool being called

        Returns:
            Tuple of (step_id, label) or None if not found
        """
        if not decision.action_plan:
            logger.debug("No action plan available for tool mapping", tool_name=tool_name)
            return None

        # Map tool names to common step ID patterns
        tool_to_step_patterns = {
            "recommend_next_session": ["recommend", "session", "workout", "next"],
            "plan_week": ["plan", "week", "weekly"],
            "plan_race_build": ["plan", "race"],
            "plan_season": ["plan", "season"],
            "adjust_training_load": ["adjust", "load", "training"],
            "explain_training_state": ["explain", "state", "training"],
            "add_workout": ["add", "workout", "log"],
        }

        patterns = tool_to_step_patterns.get(tool_name, [])
        if not patterns:
            logger.debug("No step patterns found for tool", tool_name=tool_name)
            return None

        # Try to find a matching step
        for step in decision.action_plan.steps:
            step_lower = step.label.lower()
            if any(pattern in step_lower for pattern in patterns):
                logger.info(
                    "Mapped tool to step",
                    tool_name=tool_name,
                    step_id=step.id,
                    step_label=step.label,
                )
                return (step.id, step.label)

        # Fallback: return first step if available
        if decision.action_plan.steps:
            first_step = decision.action_plan.steps[0]
            logger.info(
                "Using fallback step for tool",
                tool_name=tool_name,
                step_id=first_step.id,
                step_label=first_step.label,
            )
            return (first_step.id, first_step.label)

        logger.debug("No matching step found for tool", tool_name=tool_name)
        return None

    @staticmethod
    def _validate_intent_horizon_combination(
        intent: str,
        horizon: str | None,
    ) -> tuple[bool, str | None]:
        """Validate that intent and horizon are compatible.

        Args:
            intent: Intent classification
            horizon: Planning horizon

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Valid combinations
        valid_combinations = {
            ("recommend", "next_session"),
            ("recommend", "today"),
            ("plan", "week"),
            ("plan", "race"),
            ("plan", "season"),
            ("adjust", None),
            ("adjust", "today"),
            ("adjust", "next_session"),
            ("explain", None),
            ("explain", "today"),
            ("explain", "next_session"),
            ("log", None),
            ("log", "today"),
            ("question", None),
            ("general", None),
        }

        if (intent, horizon) not in valid_combinations:
            error_msg = (
                f"Invalid intent/horizon combination: intent={intent}, horizon={horizon}. Expected valid combination for intent '{intent}'."
            )
            return False, error_msg

        return True, None

    @staticmethod
    def _validate_step_granularity(step_label: str) -> tuple[bool, str | None]:
        """Validate that step label describes an action, not analysis.

        Args:
            step_label: Step label to validate

        Returns:
            Tuple of (is_valid, warning_message)
        """
        # Analysis keywords that indicate granularity creep
        analysis_keywords = [
            "analyzing",
            "computing",
            "calculating",
            "evaluating using",
            "applying",
            "model",
            "algorithm",
            "formula",
            "equation",
        ]

        step_lower = step_label.lower()
        for keyword in analysis_keywords:
            if keyword in step_lower:
                warning_msg = f"Step label contains analysis language: '{step_label}'. Steps should describe actions, not analysis methods."
                return False, warning_msg

        return True, None

    @staticmethod
    async def _trigger_summarization_if_needed(conversation_id: str | None) -> None:
        """Trigger conversation summarization after successful tool execution (B34).

        This is called only after successful tool execution, not during clarification loops.
        Summarization runs asynchronously and does not block the response.

        Args:
            conversation_id: Conversation ID (None if no conversation context)
        """
        logger.debug(
            "ActionExecutor: _trigger_summarization_if_needed called",
            conversation_id=conversation_id,
            has_conversation_id=bool(conversation_id),
        )
        if not conversation_id:
            logger.debug("ActionExecutor: No conversation_id, skipping summarization")
            return

        try:
            # Validate conversation_id format (must start with "c_")
            logger.debug(
                "ActionExecutor: Validating conversation_id format",
                conversation_id=conversation_id,
                starts_with_c=conversation_id.startswith("c_") if conversation_id else False,
                length=len(conversation_id) if conversation_id else 0,
            )
            if not conversation_id or not conversation_id.startswith("c_"):
                logger.debug(
                    "ActionExecutor: Invalid conversation_id format for summarization",
                    conversation_id=conversation_id,
                    reason="conversation_id must start with 'c_'",
                )
                return

            # Load slot state from conversation progress
            logger.debug(
                "ActionExecutor: Loading conversation progress for summarization",
                conversation_id=conversation_id,
            )
            progress = get_conversation_progress(conversation_id)
            slot_state = progress.slots if progress else {}
            logger.debug(
                "ActionExecutor: Conversation progress loaded",
                conversation_id=conversation_id,
                has_progress=progress is not None,
                slot_state_keys=list(slot_state.keys()) if slot_state else [],
                slot_state_count=len(slot_state) if slot_state else 0,
            )

            # Summarize conversation (incremental update)
            logger.debug(
                "ActionExecutor: Calling summarize_conversation",
                conversation_id=conversation_id,
                slot_state_keys=list(slot_state.keys()) if slot_state else [],
                slot_state_count=len(slot_state) if slot_state else 0,
            )
            summary = await summarize_conversation(
                conversation_id=conversation_id,
                slot_state=slot_state,
            )
            logger.debug(
                "ActionExecutor: summarize_conversation completed",
                conversation_id=conversation_id,
                has_summary=summary is not None,
                facts_count=len(summary.facts) if summary and hasattr(summary, "facts") else 0,
                preferences_count=len(summary.preferences) if summary and hasattr(summary, "preferences") else 0,
                open_threads_count=len(summary.open_threads) if summary and hasattr(summary, "open_threads") else 0,
            )

            # Save summary to database
            logger.debug(
                "ActionExecutor: Saving conversation summary to database",
                conversation_id=conversation_id,
                facts_count=len(summary.facts) if summary and hasattr(summary, "facts") else 0,
                preferences_count=len(summary.preferences) if summary and hasattr(summary, "preferences") else 0,
            )
            save_conversation_summary(conversation_id, summary)
            logger.debug(
                "ActionExecutor: Conversation summary saved to database",
                conversation_id=conversation_id,
            )

            logger.info(
                "Conversation summary updated after tool execution",
                conversation_id=conversation_id,
                facts_count=len(summary.facts),
                preferences_count=len(summary.preferences),
                open_threads_count=len(summary.open_threads),
            )
        except Exception as e:
            # Never fail the request due to summarization errors
            logger.debug(
                "ActionExecutor: Exception caught during summarization",
                conversation_id=conversation_id,
                error_type=type(e).__name__,
                error_message=str(e),
                error_class=type(e).__module__ + "." + type(e).__name__,
            )
            logger.warning(
                "Failed to summarize conversation after tool execution",
                conversation_id=conversation_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )

    @staticmethod
    async def execute(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute action based on orchestrator decision.

        Args:
            decision: Decision from orchestrator
            deps: Dependencies with athlete state and context
            conversation_id: Optional conversation ID for progress tracking

        Returns:
            Execution result message

        Note:
            Confidence is used for UI tone and follow-up prompts, NOT for execution logic.
            Execution proceeds regardless of confidence level.

        Design Invariant:
            No tool may execute unless the user explicitly asked for execution AND all required slots are present.

        CORE INVARIANT (HARD RULE):
            If an executable action exists and is blocked only by missing slots,
            the system MUST ask for those slots and MUST NOT chat.

        EXECUTION INVARIANT (CRITICAL):
            If slots are complete (should_execute = true), execute immediately.
            No confirmation. No waiting. Execute now.
        """
        # STATE 1: MISSING SLOTS - Ask exactly one blocking question
        # If executable action exists but slots are incomplete, ask for missing slots
        target_action = decision.target_action or decision.next_executable_action
        if target_action and decision.missing_slots and not decision.should_execute:
            # Assertion: must have missing slots to ask a question
            if len(decision.missing_slots) == 0:
                raise RuntimeError("Must have missing slots to ask question, got empty list")

            # B43: Check awaiting_slots before emitting clarification - skip if already asked
            # Load conversation progress to check if we've already asked for these slots
            already_awaiting: set[str] = set()
            if conversation_id:
                progress = get_conversation_progress(conversation_id)
                if progress:
                    already_awaiting = set(progress.awaiting_slots)

            # Filter out slots we've already asked about
            new_missing_slots = [slot for slot in decision.missing_slots if slot not in already_awaiting]

            if not new_missing_slots:
                # All missing slots already in awaiting_slots - don't emit duplicate clarification
                logger.info(
                    "B43: Skipping duplicate clarification - all slots already in awaiting_slots",
                    target_action=target_action,
                    missing_slots=decision.missing_slots,
                    awaiting_slots=list(already_awaiting),
                    conversation_id=conversation_id,
                )
                # Return a non-duplicative message or the existing awaiting status
                if decision.next_question:
                    return decision.next_question
                return "I'm still waiting for the information I asked for earlier. Please provide it when you're ready."

            logger.info(
                "STATE 1: Missing slots - asking single blocking question",
                target_action=target_action,
                missing_slots=decision.missing_slots,
                new_missing_slots=new_missing_slots,
                already_awaiting=list(already_awaiting),
                conversation_id=conversation_id,
            )
            # B43: Ask only for NEW missing slots (not already in awaiting_slots)
            # Use new_missing_slots to avoid duplicate clarification
            if decision.next_question:
                return decision.next_question
            return generate_slot_clarification(
                action=target_action,
                missing_slots=new_missing_slots,  # B43: Use filtered slots
            )

        # STATE 2: SLOTS COMPLETE - Execute immediately
        # If slots are complete, execute without confirmation
        if decision.should_execute and target_action:
            # EXECUTION GUARD: Prevent re-execution if tool already executed this turn
            has_executed = getattr(decision, "_has_executed_tool", False)
            if has_executed:
                logger.debug(
                    "ActionExecutor: Skipping execution - tool already executed this turn",
                    target_action=target_action,
                    conversation_id=conversation_id,
                )
                # Return early to prevent re-execution
                return decision.message if decision.message else "Action already executed."

            logger.debug(
                "ActionExecutor: STATE 2 - Slots complete, checking for execution",
                should_execute=decision.should_execute,
                target_action=target_action,
                missing_slots=decision.missing_slots,
                missing_slots_count=len(decision.missing_slots),
            )
            # Assertion: should_execute requires no missing slots
            if len(decision.missing_slots) > 0:
                raise RuntimeError(f"should_execute=True requires empty missing_slots, got {decision.missing_slots}")
            logger.info(
                "STATE 2: Slots complete - executing immediately",
                action=decision.action,
                target_action=target_action,
                should_execute=decision.should_execute,
                conversation_id=conversation_id,
            )
            logger.debug(
                "ActionExecutor: STATE 2 - Preparing for immediate execution",
                current_action=decision.action,
                target_action=target_action,
            )
            # Override action to EXECUTE if not already set
            if decision.action != "EXECUTE":
                logger.debug(
                    "ActionExecutor: Overriding action to EXECUTE",
                    old_action=decision.action,
                    new_action="EXECUTE",
                )
                decision.action = "EXECUTE"
            # Fall through to execution logic below

        # STATE 3: NO EXECUTABLE INTENT - Return informational response
        # If no executable action, return message as-is (allowed for informational responses)
        if not target_action:
            logger.info(
                "STATE 3: No executable intent - returning informational response",
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
            )
            return decision.message

        # If we reach here and action is still NO_ACTION, this is STATE 3 (no executable intent)
        # Return informational response without side effects
        if decision.action != "EXECUTE":
            logger.info(
                "NO_ACTION: returning informational response, no side effects",
                action=decision.action,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
            )
            return decision.message

        # Risk 2: Validate intent/horizon combination
        is_valid, error_msg = CoachActionExecutor._validate_intent_horizon_combination(
            decision.intent,
            decision.horizon,
        )
        if not is_valid:
            logger.error(
                "Invalid intent/horizon combination",
                intent=decision.intent,
                horizon=decision.horizon,
                error=error_msg,
                athlete_id=deps.athlete_id,
            )
            return "I encountered an issue processing your request. Could you try rephrasing your question or being more specific?"

        # Risk 1: Validate step granularity if action plan exists
        if decision.action_plan:
            for step in decision.action_plan.steps:
                is_valid, warning_msg = CoachActionExecutor._validate_step_granularity(step.label)
                if not is_valid:
                    logger.warning(
                        "Step granularity issue detected",
                        step_id=step.id,
                        step_label=step.label,
                        warning=warning_msg,
                        conversation_id=conversation_id,
                    )
                    # Log but don't block execution - this is a warning, not an error

        # Validate athlete_state is available for tools that need it
        if deps.athlete_state is None:
            logger.warning(
                "Cannot execute action: athlete_state is missing",
                intent=decision.intent,
                horizon=decision.horizon,
                athlete_id=deps.athlete_id,
            )
            return "I need your training data to perform this action. Please sync your activities first, or try asking a general question."

        # Execute based on target_action (preferred) or intent/horizon mapping (fallback)
        intent = decision.intent
        horizon = decision.horizon
        target_action = decision.target_action or decision.next_executable_action

        logger.debug(
            "ActionExecutor: Executing action",
            intent=intent,
            horizon=horizon,
            target_action=target_action,
            action=decision.action,
            conversation_id=conversation_id,
        )

        # ONE-TOOL-PER-TURN ASSERTION: Ensure we haven't already executed a tool
        has_executed = getattr(decision, "_has_executed_tool", False)
        if has_executed:
            raise RuntimeError("Invariant violated: multiple tool executions in a single turn")

        # Use target_action for execution routing if available
        if target_action == "plan_race_build":
            logger.debug(
                "ActionExecutor: Routing to plan_race_build execution",
                conversation_id=conversation_id,
            )
            # Mark execution flag BEFORE calling tool to prevent re-entry
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_plan_race(decision, deps, conversation_id)

        if target_action == "plan_week":
            # Special rule: Weekly planning requires a race plan
            # Check if race plan exists, if not, request race info first
            race_plan_exists = await CoachActionExecutor._check_race_plan_exists(deps.user_id, deps.athlete_id)
            if not race_plan_exists:
                logger.info(
                    "Weekly planning requires race plan - requesting race date",
                    user_id=deps.user_id,
                    athlete_id=deps.athlete_id,
                    conversation_id=conversation_id,
                )
                return "I can plan your week once your marathon plan is created. What is your marathon date?"
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_plan_week(decision, deps, conversation_id)

        # Fallback to intent/horizon mapping if no target_action
        if intent == "recommend" and horizon in {"next_session", "today"}:
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_recommend_next_session(decision, deps, conversation_id)

        if intent == "plan" and horizon == "race":
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_plan_race(decision, deps, conversation_id)

        if intent == "plan" and horizon == "season":
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_plan_season(decision, deps, conversation_id)

        if intent == "adjust":
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_adjust_training_load(decision, deps, conversation_id)

        if intent == "explain":
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_explain_training_state(decision, deps, conversation_id)

        if intent == "log":
            decision._has_executed_tool = True
            return await CoachActionExecutor._execute_add_workout(decision, deps, conversation_id)

        logger.warning(
            "Unhandled intent/horizon combination",
            intent=intent,
            horizon=horizon,
        )
        return decision.message

    @staticmethod
    async def _execute_recommend_next_session(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute recommend_next_session tool."""
        tool_name = "recommend_next_session"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        try:
            result = await call_tool(
                "recommend_next_session",
                {
                    "state": deps.athlete_state.model_dump(),
                    "user_id": deps.user_id,
                },
            )
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")
            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            return result.get("message", "Recommendation generated.")
        except MCPError as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=e.message)
            logger.exception(
                "Tool execution failed",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                    "error_code": e.code,
                },
            )
            return "Something went wrong while generating your recommendation. Please try again."
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=str(e))
            logger.exception(
                "Tool execution failed with unexpected error",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                },
            )
            return "Something went wrong while generating your recommendation. Please try again."

    @staticmethod
    async def _execute_plan_week(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute plan_week tool."""
        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to save a weekly plan. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to create a weekly plan. Please check your account settings."

        tool_name = "plan_week"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        # Extract user feedback if available (for B17/B18 constraint generation)
        user_feedback = decision.structured_data.get("user_feedback", "")
        if not user_feedback and decision.message:
            # Check if message contains feedback keywords
            feedback_keywords = ["fatigue", "tired", "sore", "pain", "wrecked", "adjust"]
            if any(keyword in decision.message.lower() for keyword in feedback_keywords):
                user_feedback = decision.message

        try:
            result = await call_tool(
                "plan_week",
                {
                    "state": deps.athlete_state.model_dump(),
                    "user_id": deps.user_id,
                    "athlete_id": deps.athlete_id,
                    "user_feedback": user_feedback if user_feedback else None,
                },
            )
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")
            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            return result.get("message", "Weekly plan created.")
        except MCPError as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=e.message)
            logger.exception(
                "Tool execution failed",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                    "error_code": e.code,
                },
            )
            return "Something went wrong while creating your weekly plan. Please try again."
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=str(e))
            logger.exception(
                "Tool execution failed with unexpected error",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                },
            )
            return "Something went wrong while creating your weekly plan. Please try again."

    @staticmethod
    def _raise_clarification_violation(tool_name: str) -> NoReturn:
        """Raise error when tool requests clarification after slot validation.

        B39: This is a developer error - tools should never request clarification
        after slots have been validated and should_execute=True.

        Args:
            tool_name: Name of the tool that violated the contract

        Raises:
            RuntimeError: Always raises to indicate developer error
        """
        error_msg = (
            f"{tool_name} requested clarification after slot validation. "
            f"This is a developer error - slots were validated before tool execution."
        )
        raise RuntimeError(error_msg)

    @staticmethod
    async def _execute_plan_race(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute plan_race_build tool."""
        logger.debug(
            "ActionExecutor: Starting plan_race_build execution",
            user_id=deps.user_id,
            athlete_id=deps.athlete_id,
            conversation_id=conversation_id,
            intent=decision.intent,
            horizon=decision.horizon,
            should_execute=decision.should_execute,
        )

        if not deps.user_id or not isinstance(deps.user_id, str):
            logger.debug("ActionExecutor: Missing user_id for plan_race_build")
            return "I need your user ID to save a race plan. Please check your account settings."
        if deps.athlete_id is None:
            logger.debug("ActionExecutor: Missing athlete_id for plan_race_build")
            return "I need your athlete ID to create a race plan. Please check your account settings."

        # Extract race description from structured_data or message
        race_description = decision.structured_data.get("race_description", "")
        if not race_description and decision.message:
            # Fallback: use message if structured_data is empty
            race_description = decision.message
        logger.debug(
            "ActionExecutor: Race description extracted",
            has_structured_data=bool(decision.structured_data.get("race_description")),
            has_message=bool(decision.message),
            description_length=len(race_description),
        )

        tool_name = "plan_race_build"

        # CRITICAL: Use decision.filled_slots (conversation slot state) instead of re-extracting
        # Decision logic already validated slots and set filled_slots from conversation slot state
        slots = decision.filled_slots

        logger.debug(
            "ActionExecutor: Using filled_slots from decision (conversation slot state)",
            slots=slots,
            slots_keys=list(slots.keys()) if slots else [],
            slots_count=len(slots) if slots else 0,
            slots_values={k: str(v) for k, v in (slots.items() if slots else [])},
            intent=decision.intent,
            horizon=decision.horizon,
            should_execute=decision.should_execute,
            conversation_id=conversation_id,
        )

        # Defensive check: if filled_slots is None or empty, fall back to clarification
        if not slots:
            logger.error(
                "ActionExecutor: filled_slots is empty despite should_execute=True - falling back to clarification",
                tool=tool_name,
                should_execute=decision.should_execute,
                target_action=decision.target_action,
                conversation_id=conversation_id,
            )
            # Fall back to asking for required slots
            required_slots = REQUIRED_SLOTS.get(tool_name, [])
            return generate_clarification_for_missing_slots(tool_name, required_slots)

        # Final validation using filled_slots (should already be validated, but double-check)
        logger.debug(
            "ActionExecutor: Validating slots for plan_race_build",
            tool=tool_name,
            slots_keys=list(slots.keys()) if slots else [],
        )
        can_execute, missing_slots = validate_slots(tool_name, slots)
        logger.debug(
            "ActionExecutor: Slot validation complete",
            tool=tool_name,
            can_execute=can_execute,
            missing_slots=missing_slots,
            missing_slots_count=len(missing_slots) if missing_slots else 0,
        )
        if not can_execute:
            # This should not happen if orchestrator logic is correct, but fail-safe check
            # Log detailed diagnostic information
            logger.error(
                "Slot validation failed despite should_execute=True - this should not happen",
                tool=tool_name,
                missing_slots=missing_slots,
                filled_slots=slots,
                filled_slots_keys=list(slots.keys()) if slots else [],
                filled_slots_values={k: str(v) if v is not None else "None" for k, v in (slots.items() if slots else [])},
                should_execute=decision.should_execute,
                target_action=decision.target_action,
                conversation_id=conversation_id,
                exc_info=True,
            )
            # Return clarification without side effects
            return generate_clarification_for_missing_slots(tool_name, missing_slots)

        # All checks passed - proceed with execution
        # Progress events are only emitted during execution
        logger.debug(
            "ActionExecutor: Finding step info for plan_race_build",
            tool=tool_name,
            has_action_plan=bool(decision.action_plan),
        )
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)
        logger.debug(
            "ActionExecutor: Step info found",
            tool=tool_name,
            has_step_info=bool(step_info),
            step_id=step_info[0] if step_info else None,
            step_label=step_info[1] if step_info else None,
        )

        if conversation_id and step_info:
            step_id, label = step_info
            logger.debug(
                "ActionExecutor: Emitting progress event (in_progress)",
                conversation_id=conversation_id,
                step_id=step_id,
                step_label=label,
            )
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        # Tool execution is wrapped defensively - never surface errors to users
        try:
            # B37: Pass filled_slots in context - tool reads ONLY from this
            # B44: Serialize filled_slots before MCP call (convert date/datetime to ISO strings)
            logger.debug(
                "ActionExecutor: Preparing tool arguments for plan_race_build",
                user_id=deps.user_id,
                athlete_id=deps.athlete_id,
                conversation_id=conversation_id,
                slots_keys=list(slots.keys()) if slots else [],
            )
            tool_args = {
                "message": race_description,
                "user_id": deps.user_id,
                "athlete_id": deps.athlete_id,
                "context": {"filled_slots": serialize_for_mcp(slots)},
            }
            # Add conversation_id if available for stateful slot tracking
            if conversation_id:
                tool_args["conversation_id"] = conversation_id

            # B47: Assert JSON safety in dev mode
            if settings.log_level == "DEBUG":
                try:
                    json.dumps(tool_args)
                    logger.debug(
                        "ActionExecutor: Tool args JSON validation passed",
                        tool=tool_name,
                        tool_args_keys=list(tool_args.keys()),
                    )
                except (TypeError, ValueError) as e:
                    logger.error(
                        "Tool args are not JSON-safe (B47)",
                        tool="plan_race_build",
                        error=str(e),
                        tool_args_keys=list(tool_args.keys()),
                        conversation_id=conversation_id,
                    )
                    raise RuntimeError(f"Tool args are not JSON-safe: {e}") from e

            logger.debug(
                "ActionExecutor: Calling MCP tool plan_race_build",
                tool=tool_name,
                tool_args_keys=list(tool_args.keys()),
                conversation_id=conversation_id,
            )
            result = await call_tool("plan_race_build", tool_args)
            logger.debug(
                "ActionExecutor: MCP tool plan_race_build completed",
                tool=tool_name,
                result_keys=list(result.keys()) if isinstance(result, dict) else None,
                has_message="message" in result if isinstance(result, dict) else False,
                conversation_id=conversation_id,
            )

            # B39: Prevent clarification after slot validation
            # If slots were complete (should_execute=True) and tool requests clarification, fail hard
            if decision.should_execute:
                result_message = result.get("message", "")
                # Check if result is a clarification message (should never happen post-validation)
                if result_message.startswith("[CLARIFICATION]") or result.get("needs_clarification"):
                    CoachActionExecutor._raise_clarification_violation(tool_name)

            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            logger.debug(
                "ActionExecutor: Triggering summarization if needed",
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            message = result.get("message", "Race plan created.")
            logger.debug(
                "ActionExecutor: plan_race_build execution complete",
                tool=tool_name,
                message_length=len(message),
                conversation_id=conversation_id,
            )
            if conversation_id and step_info:
                step_id, label = step_info
                logger.debug(
                    "ActionExecutor: Emitting progress event (completed)",
                    conversation_id=conversation_id,
                    step_id=step_id,
                    step_label=label,
                )
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")

            return message
        except MCPError as e:
            logger.debug(
                "ActionExecutor: MCPError caught in plan_race_build",
                tool=tool_name,
                error_code=e.code,
                error_message=e.message,
                conversation_id=conversation_id,
            )
            # B39: TOOL_CONTRACT_VIOLATION or MISSING_RACE_INFO should never occur after slot validation
            # If it does, this is a developer error
            if e.code == "TOOL_CONTRACT_VIOLATION":
                # B39: Tool contract violation - this is a developer error
                logger.error(
                    "Tool contract violation detected",
                    tool=tool_name,
                    error_code=e.code,
                    error_message=e.message,
                    conversation_id=conversation_id,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"{tool_name} violated contract: {e.message}. "
                    f"This is a developer error - slots should have been validated before tool execution."
                ) from e
            if e.code == "MISSING_RACE_INFO":
                if decision.should_execute:
                    raise RuntimeError(
                        f"{tool_name} returned MISSING_RACE_INFO after slot validation. "
                        f"This is a developer error - slots were validated before tool execution."
                    ) from e
                # Only allow clarification if slots weren't complete (shouldn't happen with B37)
                logger.info(
                    "Tool returned clarification request",
                    tool=tool_name,
                    conversation_id=conversation_id,
                )
                return e.message
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=e.message)
            logger.exception(
                "Tool execution failed",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                    "error_code": e.code,
                },
            )
            # Never surface tool errors to users
            return "Something went wrong while generating your plan. Please try again."
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=str(e))
            logger.exception(
                "Tool execution failed with unexpected error",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                },
            )
            # Never surface tool errors to users
            return "Something went wrong while generating your plan. Please try again."

    @staticmethod
    async def _execute_plan_season(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute plan_season tool."""
        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to save a season plan. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to create a season plan. Please check your account settings."

        # Extract season description from structured_data or message
        season_description = decision.structured_data.get("season_description", "")
        if not season_description and decision.message:
            season_description = decision.message

        tool_name = "plan_season"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        try:
            result = await call_tool(
                "plan_season",
                {
                    "message": season_description if season_description else "",
                    "user_id": deps.user_id,
                    "athlete_id": deps.athlete_id,
                },
            )
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")
            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            return result.get("message", "Season plan created.")
        except MCPError as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=e.message)
            logger.exception(
                "Tool execution failed",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                    "error_code": e.code,
                },
            )
            return "Something went wrong while creating your season plan. Please try again."
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=str(e))
            logger.exception(
                "Tool execution failed with unexpected error",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                },
            )
            return "Something went wrong while creating your season plan. Please try again."

    @staticmethod
    async def _execute_adjust_training_load(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute adjust_training_load tool."""
        # Extract user feedback from structured_data or message
        user_feedback = decision.structured_data.get("user_feedback", "")
        if not user_feedback and decision.message:
            user_feedback = decision.message

        tool_name = "adjust_training_load"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        try:
            result = await call_tool(
                "adjust_training_load",
                {
                    "state": deps.athlete_state.model_dump(),
                    "user_feedback": user_feedback,
                },
            )
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")
            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            return result.get("message", "Training load adjusted.")
        except MCPError as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=e.message)
            logger.exception(
                "Tool execution failed",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                    "error_code": e.code,
                },
            )
            return "Something went wrong while adjusting your training load. Please try again."
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=str(e))
            logger.exception(
                "Tool execution failed with unexpected error",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                },
            )
            return "Something went wrong while adjusting your training load. Please try again."

    @staticmethod
    async def _execute_explain_training_state(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute explain_training_state tool."""
        tool_name = "explain_training_state"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        try:
            result = await call_tool(
                "explain_training_state",
                {
                    "state": deps.athlete_state.model_dump(),
                },
            )
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")
            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            return result.get("message", "Training state explained.")
        except MCPError as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=e.message)
            logger.exception(
                "Tool execution failed",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                    "error_code": e.code,
                },
            )
            return "Something went wrong while explaining your training state. Please try again."
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=str(e))
            logger.exception(
                "Tool execution failed with unexpected error",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                },
            )
            return "Something went wrong while explaining your training state. Please try again."

    @staticmethod
    async def _execute_add_workout(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute add_workout tool."""
        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to save a workout. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to add a workout. Please check your account settings."

        # Extract workout description from structured_data or message
        workout_description = decision.structured_data.get("workout_description", "")
        if not workout_description and decision.message:
            workout_description = decision.message

        tool_name = "add_workout"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        try:
            result = await call_tool(
                "add_workout",
                {
                    "workout_description": workout_description,
                    "user_id": deps.user_id,
                    "athlete_id": deps.athlete_id,
                },
            )
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")
            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            return result.get("message", "Workout added successfully.")
        except MCPError as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=e.message)
            logger.exception(
                "Tool execution failed",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                    "error_code": e.code,
                },
            )
            return "Something went wrong while adding your workout. Please try again."
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "failed", message=str(e))
            logger.exception(
                "Tool execution failed with unexpected error",
                extra={
                    "tool": tool_name,
                    "conversation_id": conversation_id,
                },
            )
            return "Something went wrong while adding your workout. Please try again."

    @staticmethod
    async def _check_race_plan_exists(user_id: str | None, athlete_id: int | None) -> bool:
        """Check if a race plan (season plan) exists for the user.

        Args:
            user_id: User ID (optional)
            athlete_id: Athlete ID (optional)

        Returns:
            True if an active race/season plan exists, False otherwise
        """
        if user_id is None or athlete_id is None:
            return False

        try:
            with get_session() as session:
                result = session.execute(
                    select(SeasonPlan)
                    .where(
                        SeasonPlan.user_id == user_id,
                        SeasonPlan.athlete_id == athlete_id,
                        SeasonPlan.is_active == True,  # noqa: E712
                    )
                    .limit(1)
                )
                return result.scalar_one_or_none() is not None
        except Exception as e:
            logger.warning(f"Error checking for existing race plan: {e}")
            return False
