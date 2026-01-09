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
