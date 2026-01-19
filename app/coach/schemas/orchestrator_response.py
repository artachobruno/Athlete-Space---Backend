"""Response model for the Coach Orchestrator Agent."""

from datetime import date
from typing import Literal

from loguru import logger
from pydantic import BaseModel, model_validator

from app.coach.schemas.action_plan import ActionPlan
from app.coach.validators.execution_validator import (
    validate_execution_controller_decision,
    validate_no_advice_before_execution,
    validate_single_question,
)

ResponseType = Literal[
    "greeting",
    "question",
    "explanation",
    "plan",
    "weekly_plan",
    "season_plan",
    "session_plan",
    "recommendation",
    "summary",
]


class OrchestratorAgentResponse(BaseModel):
    """Structured decision response from the Coach Orchestrator Agent.

    The orchestrator only makes decisions - it never executes tools or performs side effects.
    All execution happens in the separate executor module.
    """

    intent: Literal["recommend", "plan", "adjust", "explain", "log", "question", "general", "clarify", "propose", "confirm", "modify"]

    horizon: Literal["today", "next_session", "week", "race", "season", None]

    action: Literal["NO_ACTION", "EXECUTE"]

    confidence: float  # 0.0-1.0 decision confidence (NOT correctness evaluation)
    # Used for UI tone and follow-up prompts, NOT execution logic

    message: str  # user-facing response

    response_type: ResponseType  # Type of response for UI rendering

    show_plan: bool = False  # Explicit signal to frontend about plan visibility

    plan_items: list[str] | None = None  # Optional list of plan items to display

    structured_data: dict = {}
    follow_up: str | None = None
    action_plan: ActionPlan | None = None

    # Execution control data (not conversational)
    # The LLM is an execution controller, not a coach.
    # It must always identify: target action, required attributes, next question.
    target_action: str | None = None  # Tool name that should execute
    required_attributes: list[str] = []  # Attributes required for target_action (orchestrator decides WHAT is needed)
    optional_attributes: list[str] = []  # Optional attributes for target_action (orchestrator decides WHAT is optional)
    filled_slots: dict[str, str | date | int | float | bool | None] = {}  # Slots filled by extractor (NOT set by orchestrator)
    missing_slots: list[str] = []  # Slots still missing (blocks execution, computed from extractor output)
    next_question: str | None = None  # Single question to remove next blocker (ONE question only, no paragraphs)
    should_execute: bool = False  # True when all slots complete - execute immediately

    # Legacy fields for compatibility (maintained for backward compatibility)
    required_slots: list[str] = []  # Deprecated: use required_attributes

    # Legacy fields (maintained for compatibility, will be deprecated)
    next_executable_action: str | None = None  # Deprecated: use target_action
    execution_confirmed: bool = False  # Deprecated: use should_execute

    @model_validator(mode="after")
    def validate_plan_usage(self) -> "OrchestratorAgentResponse":
        """Validate plan emission rules at schema level (hard gate).

        Plans are only allowed for explicit planning tasks:
        - plan, weekly_plan, season_plan, session_plan, recommendation, summary

        All other response types (greeting, question, explanation) must not emit plans.
        """
        allowed_plan_types = {
            "plan",
            "weekly_plan",
            "season_plan",
            "session_plan",
            "recommendation",
            "summary",
        }

        if self.show_plan and self.response_type not in allowed_plan_types:
            raise ValueError(
                f"show_plan is not allowed for response_type={self.response_type}. Only {allowed_plan_types} can set show_plan=True."
            )

        # If show_plan is False, ensure plan_items is None
        if not self.show_plan:
            self.plan_items = None

        return self

    @model_validator(mode="after")
    def validate_execution_controller(self) -> "OrchestratorAgentResponse":
        """Validate execution controller rules (hard gates).

        Enforces:
        1. Single-question rule when slots missing
        2. No advice before execution
        3. No chatty responses
        4. Core invariant: fill slot, ask for slot, or execute
        """
        is_valid, errors = validate_execution_controller_decision(self)

        if not is_valid:
            logger.error(
                "Execution controller validation failed",
                target_action=self.target_action,
                missing_slots=self.missing_slots,
                errors=errors,
                message_preview=self.message[:100],
            )
            # Use next_question if available and valid, otherwise keep original message
            # In production, you might want to raise here or have stricter handling
            if self.next_question:
                # Validate next_question is clean
                is_valid_q, _ = validate_single_question(self.next_question, self.missing_slots)
                is_valid_advice, _ = validate_no_advice_before_execution(self.next_question, self.target_action, self.missing_slots)
                if is_valid_q and is_valid_advice:
                    self.message = self.next_question

        return self
