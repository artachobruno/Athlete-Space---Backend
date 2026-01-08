"""MCP FS Server - HTTP server for filesystem operations.

Implements MCP-compliant tools for filesystem operations:
- load_orchestrator_prompt
- load_prompt
"""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

# Add parent directory to path to import app modules
# Handle both direct execution (python mcp/fs_server/main.py) and module execution
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Import mcp modules using direct file imports (mcp is not a package)
_fs_server_dir = Path(__file__).parent


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


# Load errors module
_errors_module = _load_module_from_path("mcp_fs_errors", _fs_server_dir / "errors.py")
MCPError = _errors_module.MCPError

# Create mock mcp.fs_server module in sys.modules so tool modules can import from it
# This allows tool modules to use "from mcp.fs_server.errors import MCPError"
# We need to create the full module hierarchy: mcp -> mcp.fs_server -> mcp.fs_server.errors
_mcp_module = types.ModuleType("mcp")
_mcp_fs_server_module = types.ModuleType("mcp.fs_server")
_mcp_fs_server_errors_module = types.ModuleType("mcp.fs_server.errors")

# Make mcp.fs_server appear as a package (needs __path__ attribute)
_mcp_fs_server_module.__path__ = [str(_fs_server_dir)]

# Copy MCPError to the errors module
_mcp_fs_server_errors_module.MCPError = MCPError

# Set up module hierarchy
_mcp_fs_server_module.errors = _mcp_fs_server_errors_module
_mcp_module.fs_server = _mcp_fs_server_module

# Register all modules in sys.modules (order matters - parent before child)
# Only register mcp if it doesn't already exist (DB server may have created it)
if "mcp" not in sys.modules:
    sys.modules["mcp"] = _mcp_module
sys.modules["mcp.fs_server"] = _mcp_fs_server_module
sys.modules["mcp.fs_server.errors"] = _mcp_fs_server_errors_module

# Load tools module
_prompts_module = _load_module_from_path("mcp_fs_prompts", _fs_server_dir / "tools" / "prompts.py")
load_orchestrator_prompt_tool = _prompts_module.load_orchestrator_prompt_tool
load_prompt_tool = _prompts_module.load_prompt_tool

app = FastAPI(title="MCP FS Server", version="1.0.0")

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
            "load_orchestrator_prompt": load_orchestrator_prompt_tool,
            "load_prompt": load_prompt_tool,
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
    return {"status": "healthy", "server": "mcp-fs-server"}


if __name__ == "__main__":
    import os

    import uvicorn

    # Host is configurable via environment variable
    # Cloud Run sets SERVER_HOST=0.0.0.0, local dev defaults to 127.0.0.1
    server_host = os.getenv("SERVER_HOST", "127.0.0.1")
    uvicorn.run(app, host=server_host, port=8081)
