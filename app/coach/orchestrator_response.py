"""Response model for the Coach Orchestrator Agent."""

from typing import Literal

from pydantic import BaseModel


class OrchestratorAgentResponse(BaseModel):
    """Structured response from the Coach Orchestrator Agent."""

    message: str
    intent: str
    response_type: Literal["tool", "conversation", "clarification"]
    structured_data: dict = {}
    follow_up: str | None = None
