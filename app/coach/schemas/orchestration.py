"""Orchestration schema for intent classification and action decision.

This is the decision layer that classifies user intent BEFORE any tool execution.
"""

from typing import Literal

from pydantic import BaseModel, Field


class OrchestrationDecision(BaseModel):
    """Orchestration decision schema.

    This is produced by the orchestrator LLM to classify user intent
    and decide what action should happen.

    Rules:
    - action=CALL_TOOL only if confidence >= 0.7
    - Only one tool per turn
    - Horizon is required for planning actions
    """

    user_intent: Literal["plan", "revise", "explain", "assess", "question"] = Field(description="Classification of user's intent")
    horizon: Literal["day", "week", "season", "none"] = Field(
        description="Time horizon for planning actions. Required for plan/revise intents."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the classification (0.0 to 1.0)",
    )
    action: Literal["NO_TOOL", "CALL_TOOL"] = Field(description="Whether to call a tool or respond conversationally")
    tool_name: Literal["plan", "revise", "none"] = Field(description="Tool to call if action=CALL_TOOL. Must be 'none' if action=NO_TOOL.")
    read_only: bool = Field(description="Whether the action is read-only (no state mutation)")
    reason: str = Field(description="Brief explanation of the decision (for debugging)")

    def model_post_init(self, __context) -> None:
        """Validate business rules after initialization."""
        # Rule: action=CALL_TOOL only if confidence >= 0.7
        if self.action == "CALL_TOOL" and self.confidence < 0.7:
            raise ValueError(f"action=CALL_TOOL requires confidence >= 0.7, got {self.confidence}")

        # Rule: tool_name must be 'none' if action=NO_TOOL
        if self.action == "NO_TOOL" and self.tool_name != "none":
            raise ValueError(f"tool_name must be 'none' when action=NO_TOOL, got {self.tool_name}")

        # Rule: tool_name must not be 'none' if action=CALL_TOOL
        if self.action == "CALL_TOOL" and self.tool_name == "none":
            raise ValueError("tool_name must not be 'none' when action=CALL_TOOL")

        # Rule: Horizon is required for planning actions
        if self.user_intent in {"plan", "revise"} and self.horizon == "none":
            raise ValueError(f"horizon is required for {self.user_intent} intent, got 'none'")
