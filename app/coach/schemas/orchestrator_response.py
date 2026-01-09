"""Response model for the Coach Orchestrator Agent."""

from typing import Literal

from pydantic import BaseModel, model_validator

from app.coach.schemas.action_plan import ActionPlan

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
