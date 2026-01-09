"""Action Executor for Coach Orchestrator.

Executes coaching actions based on orchestrator decisions.
Owns all MCP tool calls, retries, rate limiting, and safety logic.
"""

from loguru import logger

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.core.slot_extraction import extract_slots_for_intent, generate_clarification_for_missing_slots
from app.core.tool_requirements import has_required_slots


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
        """
        if decision.action != "EXECUTE":
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

        # Execute based on intent and horizon
        intent = decision.intent
        horizon = decision.horizon

        if intent == "recommend" and horizon in {"next_session", "today"}:
            return await CoachActionExecutor._execute_recommend_next_session(decision, deps, conversation_id)

        if intent == "plan" and horizon == "week":
            return await CoachActionExecutor._execute_plan_week(decision, deps, conversation_id)

        if intent == "plan" and horizon == "race":
            return await CoachActionExecutor._execute_plan_race(decision, deps, conversation_id)

        if intent == "plan" and horizon == "season":
            return await CoachActionExecutor._execute_plan_season(decision, deps, conversation_id)

        if intent == "adjust":
            return await CoachActionExecutor._execute_adjust_training_load(decision, deps, conversation_id)

        if intent == "explain":
            return await CoachActionExecutor._execute_explain_training_state(decision, deps, conversation_id)

        if intent == "log":
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
    async def _execute_plan_race(
        decision: OrchestratorAgentResponse,
        deps: CoachDeps,
        conversation_id: str | None = None,
    ) -> str:
        """Execute plan_race_build tool."""
        if not deps.user_id or not isinstance(deps.user_id, str):
            return "I need your user ID to save a race plan. Please check your account settings."
        if deps.athlete_id is None:
            return "I need your athlete ID to create a race plan. Please check your account settings."

        # Extract race description from structured_data or message
        race_description = decision.structured_data.get("race_description", "")
        if not race_description and decision.message:
            # Fallback: use message if structured_data is empty
            race_description = decision.message

        tool_name = "plan_race_build"

        # Extract slots BEFORE tool execution
        slots = extract_slots_for_intent(
            intent=decision.intent,
            horizon=decision.horizon,
            message=race_description,
            _structured_data=decision.structured_data,
        )

        logger.debug(
            "Extracted slots for plan_race_build",
            slots=slots,
            intent=decision.intent,
            horizon=decision.horizon,
        )

        # CRITICAL: Gate tool execution based on required slots
        can_execute, missing_slots = has_required_slots(tool_name, slots)
        if not can_execute:
            logger.info(
                "Tool gated due to missing slots",
                tool=tool_name,
                missing=missing_slots,
                conversation_id=conversation_id,
            )
            # Generate clarification response instead of calling tool
            return generate_clarification_for_missing_slots(tool_name, missing_slots)

        step_info = await CoachActionExecutor._find_step_id_for_tool(decision, tool_name)

        if conversation_id and step_info:
            step_id, label = step_info
            await CoachActionExecutor._emit_progress_event(conversation_id, step_id, label, "in_progress")

        # Tool execution is wrapped defensively - never surface errors to users
        try:
            result = await call_tool(
                "plan_race_build",
                {
                    "message": race_description,
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
            return result.get("message", "Race plan created.")
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
