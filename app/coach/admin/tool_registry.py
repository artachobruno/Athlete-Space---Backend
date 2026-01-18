"""Tool registry for admin control and safety.

This provides centralized configuration for all tools:
- Enable/disable tools
- Read/write permissions
- Max calls per session
- Horizon restrictions
"""

from typing import Literal

from pydantic import BaseModel


class ToolConfig(BaseModel):
    """Configuration for a single tool."""

    enabled: bool = True
    read_only: bool = False
    max_calls_per_session: int | None = None
    allowed_horizons: list[Literal["day", "week", "season"]] | None = None


class ToolRegistry:
    """Central tool registry.

    This is the single source of truth for tool permissions.
    Tools cannot run if they're disabled here, even if the LLM requests them.
    """

    def __init__(self) -> None:
        """Initialize tool registry with default configuration."""
        self._tools: dict[str, ToolConfig] = {
            "plan": ToolConfig(
                enabled=True,
                read_only=False,
                max_calls_per_session=1,
                allowed_horizons=["day", "week", "season"],
            ),
            "revise": ToolConfig(
                enabled=False,  # Disabled by default - revision uses plan tool
                read_only=False,
                max_calls_per_session=None,
                allowed_horizons=["day", "week", "season"],
            ),
            # Read-only tools (Phase 1)
            "get_completed_activities": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_planned_activities": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_athlete_profile": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_calendar_events": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_training_metrics": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            # Read-only tools (Phase 2)
            "get_plan_compliance": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_metric_trends": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_subjective_feedback": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            # Write tools (Phase 2)
            "record_subjective_feedback": ToolConfig(
                enabled=True,
                read_only=False,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            # Read-only tools (Phase 3)
            "simulate_training_load_forward": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_risk_flags": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "recommend_no_change": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            # Read-only tools (Phase 4)
            "generate_plan_rationale": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            "get_recent_decisions": ToolConfig(
                enabled=True,
                read_only=True,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
            # Write tools (Phase 4)
            "record_decision_audit": ToolConfig(
                enabled=True,
                read_only=False,
                max_calls_per_session=None,
                allowed_horizons=None,
            ),
        }

    def get_config(self, tool_name: str) -> ToolConfig | None:
        """Get configuration for a tool.

        Args:
            tool_name: Name of the tool

        Returns:
            ToolConfig if tool exists, None otherwise
        """
        return self._tools.get(tool_name)

    def is_enabled(self, tool_name: str) -> bool:
        """Check if a tool is enabled.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is enabled, False otherwise
        """
        config = self.get_config(tool_name)
        return config is not None and config.enabled

    def is_read_only(self, tool_name: str) -> bool:
        """Check if a tool is read-only.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is read-only, False otherwise
        """
        config = self.get_config(tool_name)
        return config is not None and config.read_only

    def get_max_calls(self, tool_name: str) -> int | None:
        """Get max calls per session for a tool.

        Args:
            tool_name: Name of the tool

        Returns:
            Max calls if configured, None for unlimited
        """
        config = self.get_config(tool_name)
        return config.max_calls_per_session if config else None

    def is_horizon_allowed(self, tool_name: str, horizon: Literal["day", "week", "season"]) -> bool:
        """Check if a horizon is allowed for a tool.

        Args:
            tool_name: Name of the tool
            horizon: Time horizon to check

        Returns:
            True if horizon is allowed, False otherwise
        """
        config = self.get_config(tool_name)
        if config is None:
            return False
        if config.allowed_horizons is None:
            return True  # No restrictions
        return horizon in config.allowed_horizons

    def update_config(self, tool_name: str, config: ToolConfig) -> None:
        """Update configuration for a tool.

        Args:
            tool_name: Name of the tool
            config: New configuration
        """
        self._tools[tool_name] = config

    def disable_tool(self, tool_name: str) -> None:
        """Disable a tool instantly.

        Args:
            tool_name: Name of the tool to disable
        """
        config = self.get_config(tool_name)
        if config:
            config.enabled = False
            self._tools[tool_name] = config


# Global registry instance
TOOL_REGISTRY = ToolRegistry()

# Read-only tools set (Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 6)
READ_ONLY_TOOLS = {
    "get_completed_activities",
    "get_planned_activities",
    "get_athlete_profile",
    "get_calendar_events",
    "get_training_metrics",
    "get_plan_compliance",
    "get_metric_trends",
    "get_subjective_feedback",
    "simulate_training_load_forward",
    "get_risk_flags",
    "recommend_no_change",
    "generate_plan_rationale",
    "get_recent_decisions",
    "query_coaching_knowledge",  # Phase 6: Explanation-only knowledge
}

# Write tools set (Phase 2 + Phase 4)
# These can only be executed by the executor, not directly by the coach
WRITE_TOOLS = {
    "record_subjective_feedback",
    "record_decision_audit",
}
