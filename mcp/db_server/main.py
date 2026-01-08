"""MCP DB Server - HTTP server for database operations.

Implements MCP-compliant tools for database operations:
- load_context
- save_context
- get_recent_activities
- get_yesterday_activities
- save_planned_sessions
- add_workout
- plan_race_build
- plan_season
"""

import asyncio
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Setup path for app and mcp module imports
# Handle both direct execution (python mcp/db_server/main.py) and module execution
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

_db_server_dir = Path(__file__).parent


def _load_module_from_path(module_name: str, file_path: Path) -> Any:
    """Load a module from a file path.

    Args:
        module_name: Name for the module
        file_path: Path to the Python file

    Returns:
        Loaded module

    Raises:
        RuntimeError: If the module cannot be loaded
    """
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_app_module(module_path: str) -> Any:
    """Load an app module using importlib.

    Args:
        module_path: Dot-separated module path (e.g., 'app.db.models')

    Returns:
        Loaded module
    """
    return importlib.import_module(module_path)


# Load app modules dynamically
_app_models = _load_app_module("app.db.models")
Activity = _app_models.Activity
CoachMessage = _app_models.CoachMessage
PlannedSession = _app_models.PlannedSession

_app_session = _load_app_module("app.db.session")
get_session = _app_session.get_session

# Load mcp modules using direct file imports (mcp is not a package)
_errors_module = _load_module_from_path("mcp_db_errors", _db_server_dir / "errors.py")
MCPError = _errors_module.MCPError

# Create mock mcp.db_server module in sys.modules so tool modules can import from it
# This allows tool modules to use "from mcp.db_server.errors import MCPError"
# We need to create the full module hierarchy: mcp -> mcp.db_server -> mcp.db_server.errors
_mcp_module = types.ModuleType("mcp")
_mcp_db_server_module = types.ModuleType("mcp.db_server")
_mcp_db_server_errors_module = types.ModuleType("mcp.db_server.errors")

# Make mcp.db_server appear as a package (needs __path__ attribute)
_mcp_db_server_module.__path__ = [str(_db_server_dir)]

# Copy MCPError to the errors module
_mcp_db_server_errors_module.MCPError = MCPError

# Set up module hierarchy
_mcp_db_server_module.errors = _mcp_db_server_errors_module
_mcp_module.db_server = _mcp_db_server_module

# Register all modules in sys.modules (order matters - parent before child)
sys.modules["mcp"] = _mcp_module
sys.modules["mcp.db_server"] = _mcp_db_server_module
sys.modules["mcp.db_server.errors"] = _mcp_db_server_errors_module

_tools_dir = _db_server_dir / "tools"

# Now tool modules can import from mcp.db_server.errors
_activities_module = _load_module_from_path("mcp_db_activities", _tools_dir / "activities.py")
get_recent_activities_tool = _activities_module.get_recent_activities_tool
get_yesterday_activities_tool = _activities_module.get_yesterday_activities_tool

_context_module = _load_module_from_path("mcp_db_context", _tools_dir / "context.py")
load_context_tool = _context_module.load_context_tool
save_context_tool = _context_module.save_context_tool

_sessions_module = _load_module_from_path("mcp_db_sessions", _tools_dir / "sessions.py")
add_workout_tool = _sessions_module.add_workout_tool
plan_race_build_tool = _sessions_module.plan_race_build_tool
plan_season_tool = _sessions_module.plan_season_tool
save_planned_sessions_tool = _sessions_module.save_planned_sessions_tool

app = FastAPI(title="MCP DB Server", version="1.0.0")

# Configure logger
logger.remove()
logger.add(sys.stderr, level="INFO")


def create_error_response(error_code: str, error_message: str) -> JSONResponse:
    """Create MCP-compliant error response."""
    return JSONResponse(
        status_code=200,  # MCP uses 200 with error payload
        content={
            "error": {
                "code": error_code,
                "message": error_message,
            },
        },
    )


@app.post("/mcp/tools/call")
async def call_tool(request: Request) -> JSONResponse:
    """Handle MCP tool call requests.

    Expected request body:
    {
        "tool": "tool_name",
        "arguments": {...}
    }

    Returns MCP-compliant response with result or error.
    """
    try:
        body = await request.json()
        tool_name = body.get("tool")
        arguments = body.get("arguments", {})

        if not tool_name:
            return create_error_response("INVALID_REQUEST", "Missing 'tool' field")

        # Route to appropriate tool
        tool_map = {
            "load_context": load_context_tool,
            "save_context": save_context_tool,
            "get_recent_activities": get_recent_activities_tool,
            "get_yesterday_activities": get_yesterday_activities_tool,
            "save_planned_sessions": save_planned_sessions_tool,
            "add_workout": add_workout_tool,
            "plan_race_build": plan_race_build_tool,
            "plan_season": plan_season_tool,
        }

        if tool_name not in tool_map:
            return create_error_response(
                "TOOL_NOT_FOUND",
                f"Tool '{tool_name}' not found. Available tools: {list(tool_map.keys())}",
            )

        tool_func = tool_map[tool_name]

        # Execute tool (tools are sync, but we're in async context)
        try:
            result = await asyncio.to_thread(tool_func, arguments)
            return JSONResponse(
                status_code=200,
                content={"result": result},
            )
        except MCPError as e:
            return create_error_response(e.code, e.message)
        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return create_error_response("INTERNAL_ERROR", f"Tool execution failed: {e!s}")

    except json.JSONDecodeError:
        return create_error_response("INVALID_REQUEST", "Invalid JSON in request body")
    except Exception as e:
        logger.error(f"Request handling error: {e}", exc_info=True)
        return create_error_response("INTERNAL_ERROR", f"Request handling failed: {e!s}")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "server": "mcp-db-server"}


if __name__ == "__main__":
    import os

    import uvicorn

    # Host is configurable via environment variable
    # Cloud Run sets SERVER_HOST=0.0.0.0, local dev defaults to 127.0.0.1
    server_host = os.getenv("SERVER_HOST", "127.0.0.1")
    uvicorn.run(app, host=server_host, port=8080)
