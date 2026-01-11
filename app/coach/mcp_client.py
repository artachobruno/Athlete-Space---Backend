"""MCP Client for orchestrator agent.

Handles all communication with MCP servers for database and filesystem operations.
"""

import asyncio
import inspect
import json
import os
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.config.settings import settings
from app.core.observe import trace

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
    "emit_progress_event": MCP_DB_SERVER_URL,
    # Filesystem tools
    "load_orchestrator_prompt": MCP_FS_SERVER_URL,
    "load_orchestrator_classifier_prompt": MCP_FS_SERVER_URL,
    "load_prompt": MCP_FS_SERVER_URL,
}

# HTTP client with timeout
HTTP_TIMEOUT = 30.0

# Tool-specific timeouts (in seconds)
# Hierarchical planner uses atomic LLM calls - much faster than monolithic approach
TOOL_TIMEOUTS: dict[str, float] = {
    "plan_race_build": 90.0,  # 90s for hierarchical planner (atomic calls, cached)
    "plan_season": 300.0,  # 5 minutes for season planning (still monolithic)
    "plan_week": 180.0,  # 3 minutes for week planning (legacy, not used by new planner)
    "run_analysis": 120.0,  # 2 minutes for analysis
}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create HTTP client, handling event loop closure.

    Client is created with a default timeout that can be overridden per-request.
    Per-request timeouts from TOOL_TIMEOUTS take precedence.
    """
    global _client
    if _client is None or _client.is_closed:
        # Set reasonable default timeout - per-request timeouts will override
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
    """Call an MCP tool with automatic retry on network errors.

    Retries on network/timeout errors but not on permanent errors (INVALID_INPUT, TOOL_NOT_FOUND, etc.)

    Args:
        tool_name: Name of the tool to call
        arguments: Tool arguments

    Returns:
        Tool result dictionary

    Raises:
        MCPError: If tool call fails after retries
    """
    # Get caller information for diagnostic logging
    frame = inspect.currentframe()
    caller_frame = frame.f_back if frame else None
    caller_info = "unknown"
    if caller_frame:
        caller_file = caller_frame.f_code.co_filename
        caller_line = caller_frame.f_lineno
        caller_func = caller_frame.f_code.co_name
        # Extract just the file name, not full path
        caller_file = Path(caller_file).name
        caller_info = f"{caller_file}:{caller_line}:{caller_func}"

    # Get timeout for this tool
    request_timeout = TOOL_TIMEOUTS.get(tool_name, HTTP_TIMEOUT)

    # Critical diagnostic log - shows WHO is calling and WHAT timeout
    logger.warning(
        f"MCP CALL → tool={tool_name}, phase={caller_info}, timeout={request_timeout}s",
        tool=tool_name,
        caller=caller_info,
        timeout=request_timeout,
        argument_keys=list(arguments.keys()) if isinstance(arguments, dict) else None,
    )

    logger.debug(
        "MCP: Starting tool call",
        tool=tool_name,
        argument_keys=list(arguments.keys()) if isinstance(arguments, dict) else None,
        argument_count=len(arguments) if isinstance(arguments, dict) else 0,
    )

    if tool_name not in MCP_TOOL_ROUTES:
        logger.error(
            "MCP: Tool not found in routing table",
            tool=tool_name,
            available_tools=list(MCP_TOOL_ROUTES.keys()),
        )
        raise MCPError(
            "TOOL_NOT_FOUND",
            f"Tool '{tool_name}' not found in routing table. Available tools: {list(MCP_TOOL_ROUTES.keys())}",
        )

    server_url = MCP_TOOL_ROUTES[tool_name]
    endpoint = f"{server_url}/mcp/tools/call"

    logger.debug(
        "MCP: Resolved tool route",
        tool=tool_name,
        server_url=server_url,
        endpoint=endpoint,
    )

    # Log tool call for testing (test-only, guarded by env var)
    if os.getenv("MCP_TEST_MODE") == "1":
        MCP_CALL_LOG.append(tool_name)
        logger.debug("MCP: Test mode - tool logged to MCP_CALL_LOG", tool=tool_name)

    # Note: request_timeout already set above in diagnostic logging

    # Instrument tool execution with tracing
    # Note: conversation_id and user_id are not available in call_tool signature,
    # so we use tool name only for metadata
    tool_metadata: dict[str, str] = {
        "tool": tool_name,
    }

    # Retry on network errors only (not permanent errors)
    max_retries = 3

    with trace(
        name=f"tool.{tool_name}",
        metadata=tool_metadata,
    ):
        for attempt in range(max_retries):
            logger.debug(
                "MCP: Attempting tool call",
                tool=tool_name,
                attempt=attempt + 1,
                max_retries=max_retries,
                endpoint=endpoint,
                timeout=request_timeout,
            )

            try:
                client = _get_client()
                logger.debug(
                    "MCP: HTTP client obtained",
                    tool=tool_name,
                    attempt=attempt + 1,
                    client_closed=client.is_closed,
                )

                logger.debug(
                    "MCP: Sending HTTP POST request",
                    tool=tool_name,
                    attempt=attempt + 1,
                    endpoint=endpoint,
                    timeout=request_timeout,
                    payload_keys=["tool", "arguments"],
                )

                response = await client.post(
                    endpoint,
                    json={
                        "tool": tool_name,
                        "arguments": arguments,
                    },
                    timeout=request_timeout,
                )

                logger.debug(
                    "MCP: HTTP response received",
                    tool=tool_name,
                    attempt=attempt + 1,
                    status_code=response.status_code,
                    response_headers=dict(response.headers),
                )

                response.raise_for_status()

                logger.debug(
                    "MCP: Parsing JSON response",
                    tool=tool_name,
                    attempt=attempt + 1,
                    content_length=len(response.content) if response.content else 0,
                )

                data = response.json()

                logger.debug(
                    "MCP: JSON response parsed",
                    tool=tool_name,
                    attempt=attempt + 1,
                    response_keys=list(data.keys()) if isinstance(data, dict) else None,
                    has_error="error" in data if isinstance(data, dict) else False,
                    has_result="result" in data if isinstance(data, dict) else False,
                )

                # Check for MCP error response
                if "error" in data:
                    error = data["error"]
                    error_code = error.get("code", "UNKNOWN_ERROR")
                    error_message = error.get("message", "Unknown error")

                    # Log full error response at ERROR level with all details
                    logger.error(
                        "MCP tool error - full error response",
                        tool=tool_name,
                        attempt=attempt + 1,
                        error_code=error_code,
                        error_message=error_message,
                        error_data=error,
                        full_response=data,
                    )

                    # Also log at debug level with structured data
                    logger.debug(
                        "MCP: Error response received (detailed)",
                        tool=tool_name,
                        attempt=attempt + 1,
                        error_code=error_code,
                        error_message=error_message,
                        error_data=error,
                        full_response=data,
                        error_keys=list(error.keys()) if isinstance(error, dict) else None,
                    )

                    # Print formatted error message for immediate visibility
                    logger.error(f"MCP tool error: {tool_name} - {error_code}: {error_message}")

                    # Extract and print original error details if present in message
                    if (
                        "wrapped:" in error_message
                        or "original_error" in str(error).lower()
                        or "Failed to plan race build:" in error_message
                    ):
                        logger.error(
                            "MCP error contains wrapped/original error details - printing full error chain",
                            tool=tool_name,
                            error_code=error_code,
                            error_message=error_message,
                            error_full=error,
                            full_response=data,
                        )

                    # Print the full error structure for debugging (as JSON string)
                    try:
                        error_json_str = json.dumps(data, indent=2, default=str)
                        # Log as both structured and plain text for visibility
                        logger.error(
                            "MCP error response (JSON formatted)",
                            tool=tool_name,
                            attempt=attempt + 1,
                            error_json=error_json_str,
                        )
                        # Also print directly to ensure visibility
                        print(f"\n{'=' * 80}")
                        print(f"MCP ERROR RESPONSE (tool={tool_name}, attempt={attempt + 1}):")
                        print(error_json_str)
                        print(f"{'=' * 80}\n")
                    except Exception as json_error:
                        logger.error(
                            "Failed to serialize error response to JSON",
                            tool=tool_name,
                            json_error=str(json_error),
                            error_response=data,
                        )
                        print(f"\n{'=' * 80}")
                        print("MCP ERROR RESPONSE (serialization failed):")
                        print(f"Error: {json_error}")
                        print(f"Data: {data}")
                        print(f"{'=' * 80}\n")

                    # Don't retry on permanent errors
                    permanent_errors = {
                        "TOOL_NOT_FOUND",
                        "INVALID_INPUT",
                        "INVALID_SESSION_DATA",
                        "INVALID_DATE_FORMAT",
                        "USER_NOT_FOUND",
                        "MISSING_RACE_INFO",
                        "MISSING_SEASON_INFO",
                        "INVALID_RACE_DATE",
                    }
                    if error_code in permanent_errors:
                        _raise_mcp_error(error_code, error_message)

                    # Retry on transient errors
                    if attempt < max_retries - 1:
                        wait_time = min(2**attempt, 10)  # Exponential backoff: 1s, 2s, 4s, max 10s
                        logger.warning(
                            f"MCP tool transient error: {tool_name} - {error_code}: {error_message}. "
                            f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    _raise_mcp_error(error_code, error_message)

                # Return result
                if "result" not in data:
                    _raise_mcp_error(
                        "INVALID_RESPONSE",
                        f"Missing 'result' field in MCP response for {tool_name}",
                    )

                # Success - return result
                result = data["result"]
                result_keys = list(result.keys()) if isinstance(result, dict) else None
                logger.debug(
                    "MCP: Tool call successful",
                    tool=tool_name,
                    attempt=attempt + 1,
                    result_keys=result_keys,
                    result_type=type(result).__name__,
                    result_size=len(str(result)) if result else 0,
                )
            except httpx.TimeoutException as e:
                logger.debug(
                    "MCP: Timeout exception",
                    tool=tool_name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    timeout=request_timeout,
                    error=str(e),
                )
                if attempt < max_retries - 1:
                    wait_time = min(2**attempt, 10)  # Exponential backoff: 1s, 2s, 4s, max 10s
                    logger.debug(
                        "MCP: Scheduling retry after timeout",
                        tool=tool_name,
                        attempt=attempt + 1,
                        wait_time=wait_time,
                        next_attempt=attempt + 2,
                    )
                    logger.warning(
                        f"MCP tool timeout: {tool_name}. Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})",
                        tool=tool_name,
                        timeout=request_timeout,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"MCP tool timeout after {max_retries} attempts: {tool_name}", tool=tool_name, timeout=request_timeout)
                timeout_msg = f"Tool call to {tool_name} timed out after {max_retries} attempts (timeout: {request_timeout}s)"
                raise MCPError("TIMEOUT", timeout_msg) from e
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response else None
                logger.debug(
                    "MCP: HTTP status error",
                    tool=tool_name,
                    attempt=attempt + 1,
                    status_code=status_code,
                    is_5xx=500 <= status_code < 600 if status_code else False,
                    max_retries=max_retries,
                    error=str(e),
                )
                # Retry on 5xx errors but not 4xx
                if e.response is not None and 500 <= e.response.status_code < 600 and attempt < max_retries - 1:
                    wait_time = min(2**attempt, 10)
                    logger.debug(
                        "MCP: Scheduling retry after 5xx error",
                        tool=tool_name,
                        attempt=attempt + 1,
                        status_code=status_code,
                        wait_time=wait_time,
                        next_attempt=attempt + 2,
                    )
                    logger.warning(
                        f"MCP tool HTTP error: {tool_name} - {e.response.status_code}. "
                        f"Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})",
                        tool=tool_name,
                        status_code=e.response.status_code,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                # 4xx errors (client errors) - don't retry
                logger.error(f"MCP tool HTTP error: {tool_name}", tool=tool_name, status_code=status_code)
                raise MCPError("HTTP_ERROR", f"HTTP {status_code if status_code else 'unknown'} error calling {tool_name}") from e
            except httpx.RequestError as e:
                logger.debug(
                    "MCP: Request error",
                    tool=tool_name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                if attempt < max_retries - 1:
                    wait_time = min(2**attempt, 10)
                    logger.debug(
                        "MCP: Scheduling retry after request error",
                        tool=tool_name,
                        attempt=attempt + 1,
                        wait_time=wait_time,
                        next_attempt=attempt + 2,
                    )
                    logger.warning(
                        f"MCP tool request error: {tool_name} - {e!s}. Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})",
                        tool=tool_name,
                        error=str(e),
                    )
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"MCP tool request error after {max_retries} attempts: {tool_name}", tool=tool_name, error=str(e))
                raise MCPError("NETWORK_ERROR", f"Network error calling {tool_name}: {e!s}") from e
            except MCPError:
                # Re-raise MCPError without wrapping (e.g., from _raise_mcp_error for permanent errors)
                raise
            except RuntimeError as e:
                logger.debug(
                    "MCP: RuntimeError exception",
                    tool=tool_name,
                    attempt=attempt + 1,
                    error_type=type(e).__name__,
                    error=str(e),
                    is_event_loop_error=("Event loop is closed" in str(e) or "This event loop is already running" in str(e)),
                )
                # Handle event loop closure errors
                if ("Event loop is closed" in str(e) or "This event loop is already running" in str(e)) and attempt < max_retries - 1:
                    logger.debug(
                        "MCP: Recreating client after event loop error",
                        tool=tool_name,
                        attempt=attempt + 1,
                        next_attempt=attempt + 2,
                    )
                    logger.warning(
                        f"Event loop issue calling MCP tool: {tool_name}, recreating client",
                        tool=tool_name,
                        error=str(e),
                    )
                    global _client
                    _client = None
                    wait_time = min(2**attempt, 10)
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"Unexpected RuntimeError calling MCP tool: {tool_name}", tool=tool_name, error=str(e), exc_info=True)
                raise MCPError("INTERNAL_ERROR", f"Unexpected error calling {tool_name}: {e!s}") from e
            except Exception as e:
                logger.debug(
                    "MCP: Unexpected exception",
                    tool=tool_name,
                    attempt=attempt + 1,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                # Don't retry on unknown exceptions
                logger.error(f"Unexpected error calling MCP tool: {tool_name}", tool=tool_name, error=str(e), exc_info=True)
                raise MCPError("INTERNAL_ERROR", f"Unexpected error calling {tool_name}: {e!s}") from e
            else:
                # Success path - return result
                logger.debug(
                "MCP: Returning successful result",
                tool=tool_name,
                attempt=attempt + 1,
            )
            return result

    # Should never reach here (all paths raise or return), but handle gracefully
    logger.error(
        "MCP: Unexpected control flow - tool call completed without result or exception",
        tool=tool_name,
        max_retries=max_retries,
    )
    raise MCPError("INTERNAL_ERROR", f"Unexpected error: tool call to {tool_name} completed without result or exception")
