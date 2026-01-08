"""MCP Client for orchestrator agent.

Handles all communication with MCP servers for database and filesystem operations.
"""

import os
from typing import Any

import httpx
from loguru import logger

# MCP server URLs (can be overridden via environment variables)
MCP_DB_SERVER_URL = os.getenv("MCP_DB_SERVER_URL", "http://localhost:8080")
MCP_FS_SERVER_URL = os.getenv("MCP_FS_SERVER_URL", "http://localhost:8081")

# Tool routing table
MCP_TOOL_ROUTES: dict[str, str] = {
    # Database tools
    "load_context": MCP_DB_SERVER_URL,
    "save_context": MCP_DB_SERVER_URL,
    "get_recent_activities": MCP_DB_SERVER_URL,
    "get_yesterday_activities": MCP_DB_SERVER_URL,
    "save_planned_sessions": MCP_DB_SERVER_URL,
    "add_workout": MCP_DB_SERVER_URL,
    "plan_race_build": MCP_DB_SERVER_URL,
    "plan_season": MCP_DB_SERVER_URL,
    # Filesystem tools
    "load_orchestrator_prompt": MCP_FS_SERVER_URL,
    "load_prompt": MCP_FS_SERVER_URL,
}

# HTTP client with timeout
HTTP_TIMEOUT = 30.0
_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)

# Test-only MCP call log (guarded by MCP_TEST_MODE env var)
MCP_CALL_LOG: list[str] = []


class MCPError(Exception):
    """MCP client error."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(self.message)


def _raise_mcp_error(code: str, message: str) -> None:
    """Raise MCPError with given code and message."""
    raise MCPError(code, message)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool.

    Args:
        tool_name: Name of the tool to call
        arguments: Tool arguments

    Returns:
        Tool result dictionary

    Raises:
        MCPError: If tool call fails
    """
    if tool_name not in MCP_TOOL_ROUTES:
        raise MCPError(
            "TOOL_NOT_FOUND",
            f"Tool '{tool_name}' not found in routing table. Available tools: {list(MCP_TOOL_ROUTES.keys())}",
        )

    server_url = MCP_TOOL_ROUTES[tool_name]
    endpoint = f"{server_url}/mcp/tools/call"

    # Log tool call for testing (test-only, guarded by env var)
    if os.getenv("MCP_TEST_MODE") == "1":
        MCP_CALL_LOG.append(tool_name)

    logger.debug(f"Calling MCP tool: {tool_name} at {endpoint}", tool=tool_name, arguments=arguments)

    try:
        response = await _client.post(
            endpoint,
            json={
                "tool": tool_name,
                "arguments": arguments,
            },
        )
        response.raise_for_status()

        data = response.json()

        # Check for MCP error response
        if "error" in data:
            error = data["error"]
            error_code = error.get("code", "UNKNOWN_ERROR")
            error_message = error.get("message", "Unknown error")
            logger.error(f"MCP tool error: {tool_name} - {error_code}: {error_message}")
            _raise_mcp_error(error_code, error_message)

        # Return result
        if "result" not in data:
            _raise_mcp_error(
                "INVALID_RESPONSE",
                f"Missing 'result' field in MCP response for {tool_name}",
            )

        result = data["result"]
        result_keys = list(result.keys()) if isinstance(result, dict) else None
        logger.debug(f"MCP tool success: {tool_name}", tool=tool_name, result_keys=result_keys)
    except httpx.TimeoutException as e:
        logger.error(f"MCP tool timeout: {tool_name}", tool=tool_name, timeout=HTTP_TIMEOUT)
        raise MCPError("TIMEOUT", f"Tool call to {tool_name} timed out after {HTTP_TIMEOUT}s") from e
    except httpx.HTTPStatusError as e:
        logger.error(f"MCP tool HTTP error: {tool_name}", tool=tool_name, status_code=e.response.status_code)
        raise MCPError("HTTP_ERROR", f"HTTP {e.response.status_code} error calling {tool_name}") from e
    except httpx.RequestError as e:
        logger.error(f"MCP tool request error: {tool_name}", tool=tool_name, error=str(e))
        raise MCPError("NETWORK_ERROR", f"Network error calling {tool_name}: {e!s}") from e
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error calling MCP tool: {tool_name}", tool=tool_name, error=str(e), exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Unexpected error calling {tool_name}: {e!s}") from e
    else:
        return result
