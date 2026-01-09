"""Response model for the Coach Orchestrator Agent."""

from typing import Literal

from pydantic import BaseModel, model_validator

from app.coach.schemas.action_plan import ActionPlan

ResponseType = Literal["greeting", "question", "explanation", "plan", "weekly_plan", "recommendation", "summary"]


class OrchestratorAgentResponse(BaseModel):
    """Structured decision response from the Coach Orchestrator Agent.

    The orchestrator only makes decisions - it never executes tools or performs side effects.
    All execution happens in the separate executor module.
    """

    intent: Literal["recommend", "plan", "adjust", "explain", "log", "question", "general"]

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

    @model_validator(mode="after")
    def validate_show_plan_constraint(self) -> "OrchestratorAgentResponse":
        """Validate that show_plan can only be True for specific response types."""
        allowed_response_types = {"plan", "weekly_plan", "recommendation", "summary"}
        if self.show_plan is True and self.response_type not in allowed_response_types:
            raise ValueError(
                f"show_plan can only be True for response_type in {allowed_response_types}. Got response_type='{self.response_type}'"
            )
        return self
