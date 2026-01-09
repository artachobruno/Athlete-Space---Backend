"""Response model for the Coach Orchestrator Agent."""

from typing import Literal

from pydantic import BaseModel

from app.coach.schemas.action_plan import ActionPlan


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

    structured_data: dict = {}
    follow_up: str | None = None
    action_plan: ActionPlan | None = None
