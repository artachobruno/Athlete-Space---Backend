"""Output models for the Coach Agent.

These models define the structured response format that the Coach Agent
must produce, ensuring consistency and testability.
"""

from typing import Literal

from pydantic import BaseModel


class CoachAgentResponse(BaseModel):
    """Structured coaching response from the Coach Agent.

    This replaces free-form text with structured judgment that can be
    reliably consumed by the UI and tested programmatically.
    """

    summary: str  # 1-2 sentence high-level assessment

    insights: list[str]  # bullet-level observations (max 3)

    recommendations: list[str]  # actions (max 2)

    risk_level: Literal["none", "low", "medium", "high"]

    intervention: bool  # should UI emphasize / alert?

    follow_up_prompts: list[str] | None = None  # optional constrained questions
