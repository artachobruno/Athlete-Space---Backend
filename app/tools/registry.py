"""Semantic tool registry - orchestrator-facing tool catalog.

This registry only exposes semantic tools from the canonical catalog.
Implementation details (MCP, DB, executor) are hidden.
"""

from app.tools.catalog import CANONICAL_TOOLS, ToolSpec, get_tool_spec, is_semantic_tool


class SemanticToolRegistry:
    """Registry for semantic tools only.

    This is the single source of truth for tools available to the orchestrator.
    All tools must be in the canonical catalog.
    """

    def __init__(self) -> None:
        """Initialize registry with canonical tools."""
        self._tools = CANONICAL_TOOLS.copy()

    def get_tool_spec(self, tool_name: str) -> ToolSpec | None:
        """Get tool specification by name."""
        return get_tool_spec(tool_name)

    def is_enabled(self, tool_name: str) -> bool:
        """Check if a tool is enabled (exists in catalog)."""
        return is_semantic_tool(tool_name)

    def list_tools(self) -> list[str]:
        """List all available semantic tools."""
        return list(self._tools.keys())

    def list_tools_by_tier(self, tier: str) -> list[str]:
        """List tools in a specific tier."""
        return [name for name, spec in self._tools.items() if spec.tier == tier]

    def validate_tool_name(self, tool_name: str) -> bool:
        """Validate that a tool name is a semantic tool."""
        return is_semantic_tool(tool_name)

    def get_all_specs(self) -> dict[str, ToolSpec]:
        """Get all tool specifications."""
        return self._tools.copy()


# Global semantic tool registry instance
SEMANTIC_TOOL_REGISTRY = SemanticToolRegistry()


def validate_no_duplicates() -> None:
    """Guard: Ensure no duplicate tool names exist.

    Raises:
        ValueError: If duplicates are detected
    """
    tool_names = list(CANONICAL_TOOLS.keys())
    duplicates = [name for name in tool_names if tool_names.count(name) > 1]

    if duplicates:
        raise ValueError(f"Duplicate tool names detected: {duplicates}")

    # Check for MCP-style duplicates (e.g., "plan_week" vs "plan_week (MCP)")
    mcp_suffixes = [" (MCP)", " (executor)", " (registry)"]
    for tool_name in tool_names:
        for suffix in mcp_suffixes:
            if tool_name.endswith(suffix):
                raise ValueError(f"Tool name should not include implementation suffix: {tool_name}")


# Validate on import
validate_no_duplicates()
