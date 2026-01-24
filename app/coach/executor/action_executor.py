"""Action Executor for Coach Orchestrator.

Executes coaching actions based on orchestrator decisions.
Owns all MCP tool calls, retries, rate limiting, and safety logic.
"""

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, NoReturn, Optional

from loguru import logger
from sqlalchemy import select

from app.coach.adapters.race_modification_adapter import to_race_modification
from app.coach.adapters.season_modification_adapter import adapt_extracted_season_modification
from app.coach.adapters.week_modification_adapter import to_week_modification
from app.coach.admin.tool_registry import READ_ONLY_TOOLS
from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.clarification import (
    generate_proactive_clarification,
    generate_slot_clarification,
)
from app.coach.errors import ToolContractViolationError
from app.coach.executor.errors import NoActionError
from app.coach.extraction.modify_race_extractor import extract_race_modification_llm
from app.coach.extraction.modify_season_extractor import extract_modify_season
from app.coach.extraction.modify_week_extractor import extract_week_modification_llm
from app.coach.mcp_client import MCPError, call_tool, emit_progress_event_safe
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.services.conversation_progress import get_conversation_progress
from app.coach.tools.modify_day import modify_day
from app.coach.tools.modify_race import modify_race
from app.coach.tools.modify_season import modify_season
from app.coach.tools.modify_week import modify_week
from app.config.settings import settings
from app.core.conversation_summary import save_conversation_summary, summarize_conversation
from app.core.slot_extraction import generate_clarification_for_missing_slots
from app.core.slot_gate import REQUIRED_SLOTS, validate_slots
from app.db.models import Activity, AthleteProfile, PlannedSession, PlanRevision, SeasonPlan
from app.db.session import get_session
from app.orchestrator.routing import route_with_safety_check
from app.planner.plan_day_simple import plan_single_day
from app.planner.plan_race_simple import plan_race_simple
from app.plans.modify.plan_revision_repo import list_plan_revisions
from app.plans.modify.types import DayModification
from app.plans.revision.explanation_payload import build_explanation_payload
from app.plans.revision.registry import PlanRevisionRegistry
from app.coach.policy.weekly_policy_v0 import decide_weekly_action
from app.tools.guards import require_recent_evaluation, validate_semantic_tool_only
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change
from app.tools.semantic_tool_executor import execute_semantic_tool


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
        """Emit a progress event (non-blocking, safe).

        Args:
            conversation_id: Conversation ID
            step_id: Step ID
            label: Step label
            status: Event status
            message: Optional message
        """
        await emit_progress_event_safe(
            conversation_id=conversation_id,
            step_id=step_id,
            label=label,
            status=status,
            message=message,
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
    def _enforce_revision_approval(result: dict | Any) -> None:
        """Enforce that revisions requiring approval are not applied unless explicitly approved.

        Phase 5 Invariant: No state-changing action may execute unless explicitly approved.

        This method checks if a tool result indicates that approval is required,
        and raises an error if the revision is not approved. This prevents the
        executor from treating pending revisions as successful execution.

        Args:
            result: Result dictionary from tool execution (may contain requires_approval, revision_id, or revision object)

        Raises:
            RuntimeError: If revision requires approval but is not approved

        Design:
            Tools may create revisions (propose actions).
            Only the executor may apply revisions (execute actions).
            If approval is required, the executor MUST refuse execution until approved.
        """
        if not isinstance(result, dict):
            return

        # Check for requires_approval flag in result dict
        requires_approval = result.get("requires_approval", False)
        revision_id = result.get("revision_id")

        # Also check if result contains a revision object that might have approval info
        # Some tools return revision objects directly
        revision_obj = result.get("revision")
        if revision_obj and hasattr(revision_obj, "revision_id") and not revision_id:
            # Try to get revision_id from revision object
            revision_id = getattr(revision_obj, "revision_id", None)

        # If no approval flag and no revision_id, no approval check needed
        if not requires_approval and not revision_id:
            return

        # If we have a revision_id, check the database revision for approval requirement
        if revision_id:
            with get_session() as session:
                revision = session.execute(
                    select(PlanRevision).where(PlanRevision.id == revision_id)
                ).scalar_one_or_none()

                if revision and revision.requires_approval:
                    # Check if revision is approved (status="applied" AND approved_by_user=True)
                    is_approved = (
                        revision.status == "applied"
                        and revision.approved_by_user is True
                    )

                    if not is_approved:
                        # Revision requires approval but is not approved - refuse execution
                        logger.error(
                            f"Revision {revision_id} requires approval but is not approved. "
                            f"Status: {revision.status}, approved_by_user: {revision.approved_by_user}"
                        )
                        raise RuntimeError(
                            f"Revision {revision_id} requires user approval before execution. "
                            f"Current status: {revision.status}. "
                            "Please approve the revision via the API before executing."
                        )

        # If result explicitly indicates requires_approval=True but no revision_id, this is a tool error
        if requires_approval and not revision_id:
            logger.error(
                "Tool result indicates requires_approval=True but no revision_id provided"
            )
            raise RuntimeError(
                "Tool indicated approval is required but did not provide a revision_id. "
                "This indicates a tool implementation error."
            )

    @staticmethod
    def _validate_intent_contract(
        intent: str,
        horizon: str | None,
        has_revision: bool = False,
        filled_slots: dict | None = None,
    ) -> tuple[bool, str | None]:
        """Validate intent contract before executor dispatch.

        Enforces:
        - confirm ⇒ must reference revision
        - propose ⇒ must NOT mutate (creates revision only)
        - Tier 1 intents ⇒ must never reach executor (informational only)
        - Tier 3 intents ⇒ must go through executor

        Args:
            intent: Intent classification
            horizon: Planning horizon (unused, kept for API compatibility)
            has_revision: Whether a revision exists in context
            filled_slots: Filled slots from decision (may contain revision_id)

        Returns:
            Tuple of (is_valid, error_message)
        """
        _ = horizon  # Unused but kept for API compatibility
        filled_slots = filled_slots or {}

        # Tier 1 - Informational (should not reach executor for mutation)
        if intent in {"question", "general"}:
            # These are informational only - executor should return message directly
            return True, None

        # confirm must reference revision
        if intent == "confirm":
            revision_id = filled_slots.get("revision_id")
            if not revision_id and not has_revision:
                return False, "confirm intent requires a revision_id or pending revision"

        # propose must NOT mutate (creates revision, doesn't apply)
        if intent == "propose":
            # This is validated by the tool returning requires_approval=True
            # No additional validation needed here
            pass

        # Tier 3 intents must go through executor
        if intent in {"plan", "modify", "adjust", "log", "confirm"}:
            # These are mutation intents - must use executor
            return True, None

        return True, None

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
        # Tier 1 - Informational
        # Tier 2 - Decision (no mutation)
        # Tier 3 - Mutation (may require approval)
        valid_combinations = {
            # Tier 1 - Informational
            ("question", None),
            ("general", None),
            ("explain", None),
            ("explain", "today"),
            ("explain", "next_session"),
            ("explain", "week"),  # Allow explaining training state for a week (e.g., "What workouts this week?")
            # Tier 2 - Decision (no mutation)
            ("recommend", "next_session"),
            ("recommend", "today"),
            ("propose", None),
            ("propose", "week"),
            ("propose", "race"),
            ("propose", "season"),
            ("clarify", None),
            # Tier 3 - Mutation (may require approval)
            ("plan", "week"),
            ("plan", "race"),
            ("plan", "season"),
            ("modify", "day"),
            ("modify", "week"),
            ("modify", "race"),
            ("modify", "season"),
            ("adjust", None),
            ("adjust", "today"),
            ("adjust", "next_session"),
            ("log", None),
            ("log", "today"),
            ("confirm", None),
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
            logger.exception(
                f"Failed to summarize conversation after tool execution (conversation_id={conversation_id}, error_type={type(e).__name__})"
            )

    @staticmethod
    async def execute(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute action based on orchestrator decision.

        Phase 6C: Safe for background execution - errors are logged but never crash the server.

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
        logger.info(
            "Executor entry",
            action=decision.action,
            intent=decision.intent,
            horizon=decision.horizon,
            conversation_id=conversation_id,
        )
        # Invariant guard: Legacy planner must never be called
        if "plan_race_build" in (decision.action or ""):
            raise RuntimeError("Legacy planner must never be called")

        # Phase 1: Enforce "No Writes" guarantee - read-only tools must not be executed by executor
        target_action = decision.target_action or decision.next_executable_action
        if target_action and target_action in READ_ONLY_TOOLS:
            raise RuntimeError(
                f"Read-only tool '{target_action}' must not be executed by executor. "
                "Read-only tools are for visibility only and should be called directly by orchestrator."
            )

        # Phase 6C: Safe for background execution
        # Individual _execute_* methods have try-except blocks that return error messages
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
            # TURN-SCOPED EXECUTION GUARD: Prevent duplicate execution across orchestrator re-entries
            if deps.execution_guard and deps.execution_guard.has_executed(target_action):
                logger.info(
                    "ActionExecutor: Prevented duplicate execution of tool (turn-scoped guard)",
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
            logger.info(
                f"Tool execution initiated: intent={decision.intent}, horizon={decision.horizon}, target_action={target_action}",
                intent=decision.intent,
                horizon=decision.horizon,
                target_action=target_action,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
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
        # EXCEPTION: Some intents (confirm, propose, clarify) are routed by intent, not target_action
        intent_based_routing = decision.intent in {"confirm", "propose", "clarify"}
        if not target_action and not intent_based_routing:
            logger.info(
                "STATE 3: No executable intent - returning informational response",
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
            )
            return decision.message

        # If we reach here and action is still NO_ACTION, this is STATE 3 (no executable intent)
        # Return informational response without side effects
        # EXCEPTION: intent_based_routing intents can execute even with NO_ACTION if they have should_execute=True
        if decision.action != "EXECUTE" and not (intent_based_routing and decision.should_execute):
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

        # Risk 2.5: Validate intent contract (tier semantics, revision requirements)
        has_revision = bool(decision.filled_slots and decision.filled_slots.get("revision_id"))
        is_valid, error_msg = CoachActionExecutor._validate_intent_contract(
            decision.intent,
            decision.horizon,
            has_revision=has_revision,
            filled_slots=decision.filled_slots,
        )
        if not is_valid:
            logger.error(
                "Invalid intent contract",
                intent=decision.intent,
                horizon=decision.horizon,
                has_revision=has_revision,
                error=error_msg,
                athlete_id=deps.athlete_id,
            )
            return (
                error_msg
                or "I encountered an issue processing your request. "
                "Could you try rephrasing your question or being more specific?"
            )

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

        # HARD LOCK: Use routing module - no fallback paths
        intent = decision.intent
        horizon = decision.horizon or "none"

        # Extract query type hint from message for better routing
        query_type: str | None = None
        message_lower = (decision.message or "").lower()
        if any(word in message_lower for word in ["schedule", "planned", "calendar", "what do i have", "races", "race"]):
            query_type = "schedule"
        elif any(word in message_lower for word in ["structure", "why structured", "rationale"]):
            query_type = "structure"
        elif any(word in message_lower for word in ["why", "reason"]):
            query_type = "why"

        # Route intent x horizon → semantic tool (deterministic)
        today_utc = datetime.now(timezone.utc).date()
        routed_tool, prerequisite_checks = route_with_safety_check(
            intent=intent,  # type: ignore
            horizon=horizon,  # type: ignore
            has_proposal=bool(decision.filled_slots),
            needs_approval=decision.action == "EXECUTE",
            query_type=query_type,
            user_id=deps.user_id,
            today=today_utc,
        )

        # Validate routed tool is semantic
        if routed_tool:
            validate_semantic_tool_only(routed_tool)

        # Run prerequisite checks (e.g., detect_plan_incoherence)
        for check_tool in prerequisite_checks:
            logger.info(
                "Running prerequisite check",
                check_tool=check_tool,
                intent=intent,
                horizon=horizon,
            )
            # Prerequisite checks would be executed here
            # For now, just log

        # Enforce evaluation-before-mutation invariant (PROPOSE/ADJUST only; skip for EXECUTE)
        if routed_tool and horizon in {"week", "season", "race"}:
            require_recent_evaluation(
                user_id=deps.user_id or "",
                athlete_id=deps.athlete_id or 0,
                horizon=horizon,  # type: ignore
                tool_name=routed_tool,
                action=decision.action,
            )

        # Use routed tool (enforced) or fall back to target_action for backward compatibility
        target_action = routed_tool or decision.target_action or decision.next_executable_action

        logger.debug(
            "ActionExecutor: Executing action",
            intent=intent,
            horizon=horizon,
            routed_tool=routed_tool,
            target_action=target_action,
            action=decision.action,
            conversation_id=conversation_id,
        )

        # Assert: target_action must be semantic tool if set
        if target_action:
            validate_semantic_tool_only(target_action)

        # HARD RULE: All execution must go through semantic tool executor
        # No direct tool calls - routing determines tool, executor maps to implementation
        if routed_tool:
            return await execute_semantic_tool(routed_tool, decision, deps, conversation_id)

        # If no routed tool, return informational response (no mutation)
        # This handles cases where routing returns None (e.g., question, general, clarify)
        logger.debug(
            "No routed tool - returning informational response",
            intent=intent,
            horizon=horizon,
            target_action=target_action,
        )
        return decision.message or "I understand. How can I help you with your training?"

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
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
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
    async def _execute_plan_day(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute single-day session planning.

        Uses embedding-only selection to generate exactly one training session
        for one day. No hard filters, no philosophy/phase enforcement, no fallbacks.

        Args:
            decision: Orchestrator decision
            deps: Dependencies with athlete state and context
            conversation_id: Optional conversation ID

        Returns:
            Success message with session details
        """
        tool_name = "plan_single_day"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        try:
            # Extract intent context from structured_data or message
            intent_context: dict[str, str] = {}
            structured_data = decision.structured_data or {}

            # Extract session_type, focus, etc. from structured_data
            if "session_type" in structured_data:
                intent_context["session_type"] = str(structured_data["session_type"])
            if "focus" in structured_data:
                intent_context["focus"] = str(structured_data["focus"])

            # Default domain
            domain = structured_data.get("domain", "running")
            if not isinstance(domain, str):
                domain = "running"

            # User context (optional)
            user_context: dict[str, str | int | float | None] = {}
            if deps.athlete_state:
                user_context["fitness"] = deps.athlete_state.ctl
                user_context["fatigue"] = deps.athlete_state.atl

            # Call single-day planner
            planned_session = plan_single_day(
                domain=domain,
                user_context=user_context,
                intent_context=intent_context,
            )

            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")

            logger.info(
                "Single-day session planned successfully",
                template_id=planned_session.template.template_id,
                conversation_id=conversation_id,
            )
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
            )

            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)

            # Return success message
            template_kind = planned_session.template.kind
            return f"Planned a {template_kind} session for today using template {planned_session.template.template_id}."

        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(
                    conversation_id, step_id, label, "failed", message=str(e)
                )
            logger.exception(
                "Single-day planning failed",
                tool=tool_name,
                conversation_id=conversation_id,
                error=str(e),
            )
            return "Something went wrong while planning your session. Please try again."

    @staticmethod
    def _build_day_modification_from_context(
        structured_data: dict[str, Any],
        message: str | None,
        existing_session: PlannedSession | None = None,
    ) -> Optional[DayModification]:
        """Convert unstructured modification context to structured DayModification.

        No default guesses for change_type. Returns None when change_type
        cannot be determined from structured_data or adjustment.
        """
        if "modification" in structured_data:
            mod_dict = structured_data["modification"]
            if isinstance(mod_dict, dict) and "change_type" in mod_dict:
                return DayModification(**mod_dict)

        # Infer from structured_data fields
        change_type: str | None = None
        value: float | str | dict | None = None
        reason: str | None = structured_data.get("reason")
        explicit_intent_change = structured_data.get("explicit_intent_change")

        if "change_type" in structured_data:
            change_type = str(structured_data["change_type"])
        if "value" in structured_data:
            value = structured_data["value"]

        # Fallback: infer from adjustment text
        adjustment = structured_data.get("adjustment", "")
        if isinstance(adjustment, str):
            adjustment_lower = adjustment.lower()
            if "duration" in adjustment_lower or "time" in adjustment_lower:
                change_type = "adjust_duration"
                # Try to extract numeric value (e.g., "reduce by 20%" or "30 minutes")
                # For now, default to None and let modify_day handle it
            elif "distance" in adjustment_lower or "miles" in adjustment_lower or "mileage" in adjustment_lower:
                change_type = "adjust_distance"
            elif "pace" in adjustment_lower:
                change_type = "adjust_pace"
                # Try to extract pace zone (e.g., "easy", "threshold")
                # For now, default to None
            elif "shorten" in adjustment_lower:
                change_type = "adjust_duration"
                value = None  # Will need explicit value or infer from session

        if not change_type:
            return None

        if not reason and message:
            message_lower = message.lower()
            if "tired" in message_lower or "fatigue" in message_lower:
                reason = "fatigue adjustment"
            elif "time" in message_lower or "short" in message_lower:
                reason = "time constraint"
            else:
                reason = "user request"

        return DayModification(
            change_type=change_type,  # type: ignore
            value=value,
            reason=reason,
            explicit_intent_change=explicit_intent_change,
        )

    @staticmethod
    async def _execute_modify_day(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute single-day session modification using structured modify_day().

        Uses the structured modification path which preserves intent and metrics semantics.
        This replaces the embedding-based modify_single_day() approach.

        Args:
            decision: Orchestrator decision
            deps: Dependencies with athlete state and context
            conversation_id: Optional conversation ID

        Returns:
            Success message with modification details
        """
        tool_name = "modify_day"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to modify a session. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to modify a session. Please check your account settings."

        try:
            structured_data = decision.structured_data or {}

            # Get target date (default to today)
            target_date = datetime.now(tz=timezone.utc).date()
            if "target_date" in structured_data:
                date_value = structured_data["target_date"]
                if isinstance(date_value, date):
                    target_date = date_value
                elif isinstance(date_value, str):
                    target_date = date.fromisoformat(date_value)

            # Fetch existing session to help infer modification if needed
            target_datetime_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            target_datetime_end = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=timezone.utc)

            with get_session() as db_session:
                existing_session = db_session.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.user_id == deps.user_id,
                        PlannedSession.starts_at >= target_datetime_start,
                        PlannedSession.starts_at <= target_datetime_end,
                        PlannedSession.status != "completed",
                    )
                    .order_by(PlannedSession.starts_at)
                    .limit(1)
                ).scalar_one_or_none()

                if not existing_session:
                    raise NoActionError("insufficient_modification_spec")

                # Fetch athlete profile for race day protection (orchestrator owns DB access)
                athlete_profile = db_session.execute(
                    select(AthleteProfile).where(AthleteProfile.athlete_id == deps.athlete_id)
                ).scalar_one_or_none()

                modification = CoachActionExecutor._build_day_modification_from_context(
                    structured_data=structured_data,
                    message=decision.message,
                    existing_session=existing_session,
                )
                if modification is None:
                    raise NoActionError("insufficient_modification_spec")

                # Get user request from decision message
                user_request = decision.message or f"Modify session on {target_date.isoformat()}"

                # Call structured modify_day() (pass athlete_profile from orchestrator)
                result = modify_day(
                    context={
                        "user_id": deps.user_id,
                        "athlete_id": deps.athlete_id,
                        "target_date": target_date.isoformat(),
                        "modification": modification.model_dump(),
                        "user_request": user_request,
                    },
                    athlete_profile=athlete_profile,
                )

                # Phase 5: Enforce approval requirement - refuse execution if approval needed but not granted
                CoachActionExecutor._enforce_revision_approval(result)

                # Save revision to registry
                if "revision" in result:
                    registry = PlanRevisionRegistry()
                    registry.save(result["revision"])

                if not result.get("success"):
                    error_msg = result.get("error", "Unknown error")
                    # If revision exists, could use it for explanation, but for now return error
                    return f"Could not modify session: {error_msg}"

                # Commit changes (modify_day creates new session via save_modified_session)
                db_session.commit()

            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")

            logger.info(
                "Single-day session modified successfully (structured path)",
                modification_type=modification.change_type,
                reason=modification.reason,
                conversation_id=conversation_id,
            )
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
            )

            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)

            # Return success message
            change_type_label = modification.change_type.replace("_", " ").title()
            return f"Modified your session for {target_date.isoformat()}. Change: {change_type_label}."

        except NoActionError:
            raise
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(
                    conversation_id, step_id, label, "failed", message=str(e)
                )
            logger.exception(
                "Single-day modification failed",
                tool=tool_name,
                conversation_id=conversation_id,
                error=str(e),
            )
            return "Something went wrong while modifying your session. Please try again."

    @staticmethod
    async def _execute_modify_week(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute week modification using structured modify_week().

        Uses LLM extraction to get structured intent, then applies modifications
        following structured principles (preserve intent, non-destructive, deterministic).

        Args:
            decision: Orchestrator decision
            deps: Dependencies with athlete state and context
            conversation_id: Optional conversation ID

        Returns:
            Success message with modification details
        """
        tool_name = "modify_week"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to modify a week. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to modify a week. Please check your account settings."

        try:
            # Extract structured week modification via LLM
            user_message = decision.message or ""
            today = datetime.now(tz=timezone.utc).date()

            extracted = await extract_week_modification_llm(user_message, today)

            if extracted.change_type is None:
                raise NoActionError("insufficient_modification_spec")

            # Convert extracted to structured WeekModification
            week_modification = to_week_modification(extracted, today)

            # Fetch athlete profile for race/taper protection (orchestrator owns DB access)
            athlete_profile: AthleteProfile | None = None
            with get_session() as db:
                athlete_profile = db.execute(
                    select(AthleteProfile).where(AthleteProfile.athlete_id == deps.athlete_id)
                ).scalar_one_or_none()

            # Call structured modify_week() (pass athlete_profile from orchestrator)
            result = modify_week(
                user_id=deps.user_id,
                athlete_id=deps.athlete_id,
                modification=week_modification,
                user_request=user_message,
                athlete_profile=athlete_profile,
            )

            # Phase 5: Enforce approval requirement - refuse execution if approval needed but not granted
            CoachActionExecutor._enforce_revision_approval(result)

            # Save revision to registry
            if "revision" in result:
                registry = PlanRevisionRegistry()
                registry.save(result["revision"])

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                # If revision exists, could use it for explanation, but for now return error
                return f"Could not modify week: {error_msg}"

            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")

            logger.info(
                "Week modification successful (structured path)",
                change_type=week_modification.change_type,
                session_count=len(result.get("modified_sessions", [])),
                conversation_id=conversation_id,
            )
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
            )

            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)

            # Return success message
            change_type_label = week_modification.change_type.replace("_", " ").title()
            message = result.get("message", "Week modified successfully")
            return f"{message}. Change: {change_type_label}."

        except NoActionError:
            raise
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(
                    conversation_id, step_id, label, "failed", message=str(e)
                )
            logger.exception(
                "Week modification failed",
                tool=tool_name,
                conversation_id=conversation_id,
                error=str(e),
            )
            return "Something went wrong while modifying your week. Please try again."

    @staticmethod
    async def _execute_modify_race(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute race modification using structured modify_race().

        Uses LLM extraction to get structured intent, then applies modifications
        following structured principles (metadata only, no session mutations).

        Args:
            decision: Orchestrator decision
            deps: Dependencies with athlete state and context
            conversation_id: Optional conversation ID

        Returns:
            Success message with modification details
        """
        tool_name = "modify_race"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to modify a race. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to modify a race. Please check your account settings."

        try:
            # Extract structured race modification via LLM
            user_message = decision.message or ""
            today = datetime.now(tz=timezone.utc).date()

            extracted = await extract_race_modification_llm(user_message, today)

            if extracted.change_type is None:
                raise NoActionError("insufficient_modification_spec")

            race_modification = to_race_modification(extracted, today)

            # Call structured modify_race()
            result = modify_race(
                user_id=deps.user_id,
                athlete_id=deps.athlete_id,
                modification=race_modification,
            )

            # Phase 5: Enforce approval requirement (modify_race doesn't currently use approval, but check for consistency)
            CoachActionExecutor._enforce_revision_approval(result)

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                return f"Could not modify race: {error_msg}"

            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")

            logger.info(
                "Race modification successful (structured path)",
                change_type=race_modification.change_type,
                warnings_count=len(result.get("warnings", [])),
                conversation_id=conversation_id,
            )
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
            )

            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)

            # Build success message with warnings if any
            change_type_label = race_modification.change_type.replace("_", " ").title()
            message = result.get("message", "Race modified successfully")

            warnings = result.get("warnings", [])
            if warnings:
                warnings_text = " ".join(warnings)
                return f"{message}. Change: {change_type_label}. Warnings: {warnings_text}"

            return f"{message}. Change: {change_type_label}."

        except NoActionError:
            raise
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(
                    conversation_id, step_id, label, "failed", message=str(e)
                )
            logger.exception(
                "Race modification failed",
                tool=tool_name,
                conversation_id=conversation_id,
                error=str(e),
            )
            return f"Something went wrong while modifying your race: {e!s}"

    @staticmethod
    async def _execute_modify_season(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute season modification using structured modify_season().

        Uses LLM extraction to get structured intent, then applies modifications
        following structured principles (preserve intent, non-destructive, deterministic).

        Args:
            decision: Orchestrator decision
            deps: Dependencies with athlete state and context
            conversation_id: Optional conversation ID

        Returns:
            Success message with modification details
        """
        tool_name = "modify_season"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to modify a season. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to modify a season. Please check your account settings."

        try:
            # Extract structured season modification via LLM
            user_message = decision.message or ""

            extracted = await extract_modify_season(user_message)

            if extracted.change_type is None:
                raise NoActionError("insufficient_modification_spec")

            # Convert extracted to structured SeasonModification
            season_modification = adapt_extracted_season_modification(
                extracted,
                athlete_id=deps.athlete_id,
            )

            # Call structured modify_season()
            result = modify_season(
                user_id=deps.user_id,
                athlete_id=deps.athlete_id,
                modification=season_modification,
            )

            # Phase 5: Enforce approval requirement (modify_season delegates to modify_week which handles approval)
            CoachActionExecutor._enforce_revision_approval(result)

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                return f"Could not modify season: {error_msg}"

            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")

            logger.info(
                "Season modification successful (structured path)",
                change_type=season_modification.change_type,
                session_count=len(result.get("modified_sessions", [])),
                conversation_id=conversation_id,
            )
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
            )

            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)

            # Return success message
            change_type_label = season_modification.change_type.replace("_", " ").title()
            message = result.get("message", "Season modified successfully")
            return f"{message}. Change: {change_type_label}."

        except NoActionError:
            raise
        except Exception as e:
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(
                    conversation_id, step_id, label, "failed", message=str(e)
                )
            logger.exception(
                "Season modification failed",
                tool=tool_name,
                conversation_id=conversation_id,
                error=str(e),
            )
            return "Something went wrong while modifying your season. Please try again."

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
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
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
            extra={
                "user_id": deps.user_id,
                "athlete_id": deps.athlete_id,
                "conversation_id": conversation_id,
                "intent": decision.intent,
                "horizon": decision.horizon,
                "should_execute": decision.should_execute,
            },
        )

        # FIX 4: Hard guard - crash loudly if execution is attempted with missing slots
        # This ensures orchestrator bugs never reach MCP, failures are obvious not silent
        required = decision.required_attributes or []
        slots = decision.filled_slots or {}

        missing = [r for r in required if r not in slots or slots[r] in (None, "", [])]

        if missing:
            error_msg = f"plan_race_build called with missing slots: {missing}"
            logger.error(
                "ActionExecutor: Hard guard fired - missing required slots",
                extra={
                    "tool": "plan_race_build",
                    "required_attributes": required,
                    "filled_slots_keys": list(slots.keys()),
                    "missing_slots": missing,
                    "should_execute": decision.should_execute,
                    "conversation_id": conversation_id,
                },
            )
            raise RuntimeError(error_msg)

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
            error_msg = (
                f"ActionExecutor: Validation error "
                f"(conversation_id={conversation_id}, "
                f"should_execute={decision.should_execute}, "
                f"target_action={decision.target_action})"
            )
            logger.error(
                error_msg,
                tool=tool_name,
                missing_slots=missing_slots,
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

        # Helper functions for error handling
        def _raise_invalid_type_error(field_name: str, field_type: type) -> NoReturn:
            raise TypeError(f"Invalid {field_name} type: {field_type}")

        # Tool execution is wrapped defensively - never surface errors to users
        try:
            # FIX 7: Invariant logging before execution (temporary but recommended)
            logger.info(
                "Execution check",
                extra={
                    "target_action": decision.target_action,
                    "filled_slots": slots,
                    "missing_slots": decision.missing_slots,
                    "should_execute": decision.should_execute,
                },
            )

            # FIX 5: Never send empty message to MCP - MCP tools require a semantic message
            # MCP is message-driven, empty string is invalid
            if not race_description:
                # Construct message from slots if original message is empty
                race_distance = slots.get("race_distance", "race")
                race_date = slots.get("race_date", "target date")
                if isinstance(race_date, date):
                    race_date_str = race_date.strftime("%B %d, %Y")
                else:
                    race_date_str = str(race_date)
                race_description = f"Build a training plan for a {race_distance} race on {race_date_str}."
                logger.debug(
                    "ActionExecutor: Constructed message from slots",
                    constructed_message=race_description,
                )

            # Extract race_date and race_distance from slots
            race_date_value = slots.get("race_date")
            race_distance = slots.get("race_distance")

            if not race_date_value:
                raise RuntimeError("race_date is required but missing from filled_slots")
            if not race_distance:
                raise RuntimeError("race_distance is required but missing from filled_slots")

            # Parse race_date - it might be a string, date, or datetime
            if isinstance(race_date_value, str):
                # Try parsing ISO format
                try:
                    race_date = datetime.fromisoformat(race_date_value.replace("Z", "+00:00"))
                except ValueError:
                    # Try parsing date format (YYYY-MM-DD)
                    try:
                        parsed_date = date.fromisoformat(race_date_value)
                        race_date = datetime.combine(parsed_date, datetime.min.time())
                    except ValueError as e:
                        raise RuntimeError(f"Invalid race_date format: {race_date_value}") from e
            elif isinstance(race_date_value, date):
                race_date = datetime.combine(race_date_value, datetime.min.time())
            elif isinstance(race_date_value, datetime):
                race_date = race_date_value
            else:
                _raise_invalid_type_error("race_date", type(race_date_value))

            if not isinstance(race_distance, str):
                _raise_invalid_type_error("race_distance", type(race_distance))

            logger.debug(
                "ActionExecutor: Calling plan_race_simple directly",
                user_id=deps.user_id,
                athlete_id=deps.athlete_id,
                race_date=race_date.isoformat(),
                race_distance=race_distance,
                conversation_id=conversation_id,
            )

            # Call plan_race_simple directly - no MCP, no retries, no background task
            t_plan_start = time.monotonic()
            try:
                await plan_race_simple(
                    race_date=race_date,
                    distance=race_distance,
                    user_id=deps.user_id,
                    athlete_id=deps.athlete_id,
                    athlete_state=deps.athlete_state,
                )
            finally:
                t_plan_end = time.monotonic()
                plan_generation_time = t_plan_end - t_plan_start
                logger.info(f"[PLAN] plan_generation={plan_generation_time:.1f}s")

            logger.info(
                "Plan generation completed",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
            )
            logger.debug(
                "ActionExecutor: Triggering summarization if needed",
                conversation_id=conversation_id,
            )
            # Trigger summarization after successful plan generation (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            message = "Your training plan has been generated and saved to your calendar!"
            logger.debug(
                "ActionExecutor: plan_race_build execution completed",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            return message
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
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
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
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
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
    async def _format_week_workouts(user_id: str, training_state_msg: str) -> str:
        """Format scheduled workouts for the current week with coaching feedback.

        Args:
            user_id: User ID to fetch sessions for
            training_state_msg: Base training state message

        Returns:
            Message with training state, scheduled workouts, and coaching feedback
        """
        try:
            now = datetime.now(timezone.utc)
            days_since_monday = now.weekday()
            monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
            sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

            sessions_result = await call_tool(
                "get_planned_sessions",
                {
                    "user_id": user_id,
                    "start_date": monday.isoformat(),
                    "end_date": sunday.isoformat(),
                },
            )

            sessions_data = sessions_result.get("sessions", [])

            if sessions_data:
                # Format workouts conversationally
                sessions_list_parts = []
                for s in sessions_data[:10]:  # Limit to 10 to avoid too long responses
                    session_name = s.get("name", "Workout")
                    starts_at = s.get("starts_at", "")
                    if starts_at:
                        # Parse date for friendly formatting
                        try:
                            dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                            day_name = dt.strftime("%A")
                            date_str = dt.strftime("%B %d")
                            sessions_list_parts.append(f"{session_name} on {day_name}, {date_str}")
                        except Exception:
                            sessions_list_parts.append(f"{session_name} on {starts_at[:10]}")
                    else:
                        sessions_list_parts.append(session_name)

                sessions_text = "\n".join(f"• {s}" for s in sessions_list_parts)

                # Generate coaching feedback based on schedule
                feedback_parts = []

                # Assess schedule density
                session_count = len(sessions_data)

                if session_count >= 5:
                    feedback_parts.append("You have a solid week planned with good structure.")
                elif session_count >= 3:
                    feedback_parts.append("Your week has a reasonable training load.")
                else:
                    feedback_parts.append("Your schedule looks light this week—consider adding more volume if you're building fitness.")

                # Assess recovery days
                if session_count <= 2:
                    feedback_parts.append("Make sure you're maintaining consistency with your training plan.")
                elif session_count <= 6:
                    recovery_day_names = {"rest", "recovery", "easy", "off"}
                    has_recovery_day = any(
                        s.get("name", "").lower() in recovery_day_names for s in sessions_data
                    )
                    if not has_recovery_day:
                        feedback_parts.append("Consider scheduling a recovery day to absorb training stress.")

                feedback_text = " ".join(feedback_parts) if feedback_parts else ""

                response = f"{training_state_msg}\n\nHere's what you have scheduled this week:\n{sessions_text}"
                if feedback_text:
                    response += f"\n\n{feedback_text}"

                return response

            return f"{training_state_msg}\n\nYou don't have any workouts scheduled for this week yet."

        except Exception as e:
            logger.warning(f"Failed to fetch scheduled workouts: {e}", exc_info=True)
            # Use training state even if scheduled workouts fetch fails
            return training_state_msg

    @staticmethod
    async def _execute_preview_plan_change(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Preview plan change: evaluate + policy only. No DB writes, no mutation.

        Read-only. Calls evaluate_plan_change(store=False) and policy engine.
        Returns decision, reasons, recommended_actions, confidence.
        """
        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to preview plan changes."
        if deps.athlete_id is None:
            return "I need your athlete ID to preview plan changes."

        today = datetime.now(timezone.utc).date()
        horizon = decision.horizon if decision.horizon in ("week", "season", "race") else "week"

        eval_result = evaluate_plan_change(
            user_id=deps.user_id,
            athlete_id=deps.athlete_id,
            horizon=horizon,
            today=today,
            store=False,
        )
        policy_result = decide_weekly_action(eval_result.current_state)

        decision_str = eval_result.decision.decision
        reasons = eval_result.decision.reasons or []
        recommended = eval_result.decision.recommended_actions or []
        confidence = eval_result.decision.confidence

        parts = [
            f"Preview (policy: {policy_result.decision}): {policy_result.reason}",
            f"Evaluation: {decision_str} (confidence: {confidence:.0%}).",
        ]
        if reasons:
            parts.append("Reasons: " + "; ".join(reasons))
        if recommended:
            parts.append("Recommended: " + "; ".join(recommended))

        return " ".join(parts)

    @staticmethod
    async def _execute_get_planned_sessions(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute get_planned_sessions tool to retrieve and format planned sessions.

        Args:
            decision: Orchestrator decision
            deps: Dependencies
            conversation_id: Optional conversation ID

        Returns:
            Formatted message with planned sessions
        """
        tool_name = "get_planned_sessions"
        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        try:
            # Determine date range based on horizon
            now = datetime.now(timezone.utc)
            horizon = decision.horizon or "week"

            if horizon == "today":
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            elif horizon == "week":
                days_since_monday = now.weekday()
                start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)
            else:
                # Default to current week for other horizons
                days_since_monday = now.weekday()
                start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)

            sessions_result = await call_tool(
                "get_planned_sessions",
                {
                    "user_id": deps.user_id,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )

            sessions_data = sessions_result.get("sessions", [])

            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")

            if not sessions_data:
                if horizon == "today":
                    return "You don't have any workouts scheduled for today."
                return "You don't have any workouts scheduled for this week yet."

            # Format sessions conversationally
            sessions_list_parts = []
            for s in sessions_data[:20]:  # Limit to 20 to avoid too long responses
                session_name = s.get("name", "Workout")
                starts_at = s.get("starts_at", "")
                if starts_at:
                    try:
                        dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                        day_name = dt.strftime("%A")
                        date_str = dt.strftime("%B %d")
                        time_str = dt.strftime("%I:%M %p").lstrip("0")
                        sessions_list_parts.append(f"{session_name} on {day_name}, {date_str} at {time_str}")
                    except Exception:
                        sessions_list_parts.append(f"{session_name} on {starts_at[:10]}")
                else:
                    sessions_list_parts.append(session_name)

            sessions_text = "\n".join(f"• {s}" for s in sessions_list_parts)

            if horizon == "today":
                return f"Here's what you have scheduled for today:\n{sessions_text}"
            return f"Here's what you have scheduled this week:\n{sessions_text}"

        except Exception as e:
            logger.warning(f"Failed to fetch planned sessions: {e}", exc_info=True)
            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "error")
            return "I couldn't retrieve your planned sessions at the moment. Please try again later."

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
            training_state_msg = result.get("message", "Training state explained.")

            # Check if user is asking about scheduled workouts
            # Look for keywords in the message or check horizon
            message_lower = decision.message.lower()
            workout_keywords = ["scheduled", "workout", "workouts", "calendar", "what do i have", "what's on"]
            is_workout_query = any(keyword in message_lower for keyword in workout_keywords)

            # Fetch scheduled workouts if horizon is "week" OR if query is about workouts/calendar
            final_message = training_state_msg
            if deps.user_id and (decision.horizon == "week" or is_workout_query):
                final_message = await CoachActionExecutor._format_week_workouts(deps.user_id, training_state_msg)

            if conversation_id and step_info:
                step_id, label = step_info
                await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "completed")
            logger.info(
                "Tool executed successfully",
                tool=tool_name,
                conversation_id=conversation_id,
            )
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
            )
            # Trigger summarization after successful tool execution (B34)
            await CoachActionExecutor._trigger_summarization_if_needed(conversation_id)
            return final_message
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
    async def _execute_confirm_revision(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute confirm intent - approve and apply a pending revision.

        Phase 5 Invariant: A confirmed change can never apply without a pending revision.

        Flow:
        1. Require explicit revision reference (revision_id OR "latest pending revision")
        2. Validate revision state (must exist, status="pending", belongs to user/athlete)
        3. Apply via rollback-safe path (update status, approved_by_user, applied, applied_at)
        4. Write audit entry (intent=confirm, actor=user, revision_id)

        Args:
            decision: Orchestrator decision with revision_id in filled_slots
            deps: Dependencies
            conversation_id: Optional conversation ID

        Returns:
            Success message or clarification if validation fails
        """
        # Step 1: Require explicit revision reference
        revision_id = None
        if decision.filled_slots:
            revision_id = decision.filled_slots.get("revision_id")

        # If no explicit revision_id, try "latest pending revision"
        if not revision_id:
            if not deps.athlete_id:
                return "I need to know which revision you want to confirm. Please specify the revision ID."

            # Get latest pending revision for this athlete
            with get_session() as session:
                pending_revisions = [
                    r for r in list_plan_revisions(session, athlete_id=deps.athlete_id)
                    if r.status == "pending" and r.requires_approval
                ]

                if not pending_revisions:
                    return "I don't see any pending revisions to confirm. Would you like me to propose a change first?"

                # Get most recent pending revision
                latest_pending = max(pending_revisions, key=lambda r: r.created_at)
                revision_id = latest_pending.id
                logger.info(
                    "Using latest pending revision for confirm",
                    revision_id=revision_id,
                    athlete_id=deps.athlete_id,
                    conversation_id=conversation_id,
                )

        # Step 2: Validate revision state
        if not deps.user_id or not deps.athlete_id:
            return "I need your user and athlete information to confirm a revision."

        try:
            with get_session() as session:
                revision = session.execute(
                    select(PlanRevision).where(PlanRevision.id == revision_id)
                ).scalar_one_or_none()

                if not revision:
                    return f"I couldn't find revision {revision_id}. Please check the revision ID and try again."

                # Validate ownership
                if revision.user_id != deps.user_id:
                    return "This revision doesn't belong to your account. I can only confirm revisions for your own plans."

                if revision.athlete_id != deps.athlete_id:
                    return "This revision is for a different athlete. I can only confirm revisions for your current athlete profile."

                # Validate status
                if revision.status != "pending":
                    return {
                        "applied": "This revision has already been applied.",
                        "blocked": "This revision was blocked and cannot be applied.",
                    }.get(
                        revision.status,
                        f"This revision has status '{revision.status}' and cannot be confirmed.",
                    )

                if not revision.requires_approval:
                    return "This revision doesn't require approval. It should have been applied automatically."

                # Step 3: Apply via rollback-safe path
                # Update revision to approved and applied
                revision.status = "applied"
                revision.approved_by_user = True
                revision.applied = True
                revision.applied_at = datetime.now(timezone.utc)

                session.commit()

                # Step 4: Write audit entry (revision already updated above)
                logger.info(
                    "Revision confirmed and applied",
                    revision_id=revision_id,
                    user_id=deps.user_id,
                    athlete_id=deps.athlete_id,
                    revision_type=revision.revision_type,
                    conversation_id=conversation_id,
                    intent="confirm",
                )

                # The actual application of the revision's changes happens when the tool
                # that created the revision is re-executed (it will see approved_by_user=True
                # and proceed with applying the changes)

                return "Got it — I've confirmed and applied the revision. The changes are now active in your plan."

        except Exception as e:
            logger.exception(
                "Failed to confirm revision",
                revision_id=revision_id,
                user_id=deps.user_id,
                athlete_id=deps.athlete_id,
                conversation_id=conversation_id,
                error=str(e),
            )
            return "Something went wrong while confirming the revision. Please try again."

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
            logger.info(
                f"Tool executed: tool={tool_name}, intent={decision.intent}, horizon={decision.horizon}",
                tool=tool_name,
                intent=decision.intent,
                horizon=decision.horizon,
                conversation_id=conversation_id,
                athlete_id=deps.athlete_id,
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
