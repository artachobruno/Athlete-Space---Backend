"""MCP adapter for semantic tools.

This adapter wraps all MCP tool calls. Semantic tools should never
directly import or call MCP client code.
"""

from typing import Any

from loguru import logger

from app.coach.mcp_client import MCPError, call_tool


async def call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool via adapter.

    Args:
        tool_name: MCP tool name
        arguments: Tool arguments

    Returns:
        Tool result dictionary

    Raises:
        MCPError: If tool call fails
    """
    logger.debug(
        "MCP adapter: calling tool",
        tool=tool_name,
        argument_keys=list(arguments.keys()),
    )
    return await call_tool(tool_name, arguments)


async def call_mcp_tool_safe(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Call an MCP tool safely (returns None on error).

    Args:
        tool_name: MCP tool name
        arguments: Tool arguments

    Returns:
        Tool result dictionary or None on error
    """
    try:
        return await call_mcp_tool(tool_name, arguments)
    except MCPError as e:
        logger.warning(
            "MCP adapter: tool call failed",
            tool=tool_name,
            error_code=e.code,
            error_message=e.message,
        )
        return None
    except Exception as e:
        logger.exception(
            "MCP adapter: unexpected error",
            tool=tool_name,
            error=str(e),
        )
        return None
