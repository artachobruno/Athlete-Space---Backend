"""MCP Client for orchestrator agent.

Handles all communication with MCP servers for database and filesystem operations.
"""

import os
from typing import Any

import httpx
from loguru import logger

from app.config.settings import settings

# MCP server URLs (from validated Settings)
MCP_DB_SERVER_URL = settings.mcp_db_server_url
MCP_FS_SERVER_URL = settings.mcp_fs_server_url

# Tool routing table
MCP_TOOL_ROUTES: dict[str, str] = {
    # Database tools
    "load_context": MCP_DB_SERVER_URL,
    "save_context": MCP_DB_SERVER_URL,
    "get_recent_activities": MCP_DB_SERVER_URL,
    "get_yesterday_activities": MCP_DB_SERVER_URL,
    "save_planned_sessions": MCP_DB_SERVER_URL,
    "get_planned_sessions": MCP_DB_SERVER_URL,
    "add_workout": MCP_DB_SERVER_URL,
    "plan_race_build": MCP_DB_SERVER_URL,
    "plan_season": MCP_DB_SERVER_URL,
    "plan_week": MCP_DB_SERVER_URL,
    "run_analysis": MCP_DB_SERVER_URL,
    "explain_training_state": MCP_DB_SERVER_URL,
    "adjust_training_load": MCP_DB_SERVER_URL,
    "recommend_next_session": MCP_DB_SERVER_URL,
    "share_report": MCP_DB_SERVER_URL,
    # Filesystem tools
    "load_orchestrator_prompt": MCP_FS_SERVER_URL,
    "load_prompt": MCP_FS_SERVER_URL,
}

# HTTP client with timeout
HTTP_TIMEOUT = 30.0
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create HTTP client, handling event loop closure."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    return _client


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
        client = _get_client()
        response = await client.post(
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
    except RuntimeError as e:
        # Handle event loop closure errors
        if "Event loop is closed" in str(e) or "This event loop is already running" in str(e):
            logger.warning(
                f"Event loop issue calling MCP tool: {tool_name}, recreating client",
                tool=tool_name,
                error=str(e),
            )
            global _client
            _client = None
            # Retry once with new client
            try:
                client = _get_client()
                response = await client.post(
                    endpoint,
                    json={
                        "tool": tool_name,
                        "arguments": arguments,
                    },
                )
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    error = data["error"]
                    error_code = error.get("code", "UNKNOWN_ERROR")
                    error_message = error.get("message", "Unknown error")
                    logger.error(f"MCP tool error: {tool_name} - {error_code}: {error_message}")
                    _raise_mcp_error(error_code, error_message)
                if "result" not in data:
                    _raise_mcp_error(
                        "INVALID_RESPONSE",
                        f"Missing 'result' field in MCP response for {tool_name}",
                    )
                result = data["result"]
                result_keys = list(result.keys()) if isinstance(result, dict) else None
                logger.debug(f"MCP tool success: {tool_name}", tool=tool_name, result_keys=result_keys)
            except Exception as retry_e:
                logger.error(
                    f"Retry failed for MCP tool: {tool_name}",
                    tool=tool_name,
                    error=str(retry_e),
                    exc_info=True,
                )
                raise MCPError("INTERNAL_ERROR", f"Unexpected error calling {tool_name}: {retry_e!s}") from retry_e
            else:
                return result
        logger.error(f"Unexpected error calling MCP tool: {tool_name}", tool=tool_name, error=str(e), exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Unexpected error calling {tool_name}: {e!s}") from e
    except Exception as e:
        logger.error(f"Unexpected error calling MCP tool: {tool_name}", tool=tool_name, error=str(e), exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Unexpected error calling {tool_name}: {e!s}") from e
    else:
        # This block executes only if no exception was raised in the try block
        result = data["result"]
        result_keys = list(result.keys()) if isinstance(result, dict) else None
        logger.debug(f"MCP tool success: {tool_name}", tool=tool_name, result_keys=result_keys)
        return result
