"""Prompt loading tools for MCP FS server."""

import sys
from pathlib import Path
from typing import NoReturn

from loguru import logger

from mcp.fs_server.errors import MCPError


def _raise_file_not_found(path: str) -> NoReturn:
    """Raise MCPError for file not found."""
    raise MCPError("FILE_NOT_FOUND", f"Prompt file not found: {path}") from None


def _raise_invalid_filename(message: str) -> NoReturn:
    """Raise MCPError for invalid filename."""
    raise MCPError("INVALID_FILENAME", message) from None


# Base directory for prompts (relative to project root)
PROMPTS_BASE_DIR = Path(__file__).parent.parent.parent.parent / "app" / "coach" / "prompts"
ORCHESTRATOR_PROMPT_PATH = PROMPTS_BASE_DIR / "orchestrator.txt"


def load_orchestrator_prompt_tool(arguments: dict) -> dict:
    """Load the orchestrator prompt file.

    Contract: load_orchestrator_prompt.json
    """
    # No arguments expected for this tool
    if arguments:
        logger.warning(f"Unexpected arguments provided to load_orchestrator_prompt: {arguments}")

    try:
        if not ORCHESTRATOR_PROMPT_PATH.exists():
            _raise_file_not_found(str(ORCHESTRATOR_PROMPT_PATH))

        content = ORCHESTRATOR_PROMPT_PATH.read_text(encoding="utf-8")

        logger.info(f"Loaded orchestrator prompt ({len(content)} bytes)")
    except FileNotFoundError:
        _raise_file_not_found(str(ORCHESTRATOR_PROMPT_PATH))
    except UnicodeDecodeError as e:
        logger.error(f"Encoding error reading orchestrator prompt: {e}", exc_info=True)
        raise MCPError("ENCODING_ERROR", f"Failed to decode prompt file: {e!s}") from e
    except PermissionError as e:
        logger.error(f"Permission error reading orchestrator prompt: {e}", exc_info=True)
        raise MCPError("READ_ERROR", f"Permission denied reading prompt file: {e!s}") from e
    except Exception as e:
        logger.error(f"Unexpected error reading orchestrator prompt: {e}", exc_info=True)
        raise MCPError("READ_ERROR", f"Failed to read prompt file: {e!s}") from e
    else:
        return {"content": content}


def load_prompt_tool(arguments: dict) -> dict:
    """Load a prompt file by filename.

    Contract: load_prompt.json
    """
    filename = arguments.get("filename")

    # Validate inputs
    if not filename or not isinstance(filename, str):
        raise MCPError("INVALID_FILENAME", "Missing or invalid filename")

    # Security: Validate filename to prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise MCPError("INVALID_FILENAME", "Filename contains invalid characters (path traversal not allowed)")

    # Only allow .txt files
    if not filename.endswith(".txt"):
        raise MCPError("INVALID_FILENAME", "Only .txt files are allowed")

    try:
        prompt_path = PROMPTS_BASE_DIR / filename

        # Additional security: Ensure path is within prompts directory
        try:
            prompt_path.resolve().relative_to(PROMPTS_BASE_DIR.resolve())
        except ValueError:
            _raise_invalid_filename("Filename resolves outside prompts directory")

        if not prompt_path.exists():
            _raise_file_not_found(filename)

        content = prompt_path.read_text(encoding="utf-8")

        logger.info(f"Loaded prompt file {filename} ({len(content)} bytes)")
    except MCPError:
        raise
    except FileNotFoundError:
        _raise_file_not_found(filename)
    except UnicodeDecodeError as e:
        logger.error(f"Encoding error reading prompt {filename}: {e}", exc_info=True)
        raise MCPError("ENCODING_ERROR", f"Failed to decode prompt file: {e!s}") from e
    except PermissionError as e:
        logger.error(f"Permission error reading prompt {filename}: {e}", exc_info=True)
        raise MCPError("READ_ERROR", f"Permission denied reading prompt file: {e!s}") from e
    except Exception as e:
        logger.error(f"Unexpected error reading prompt {filename}: {e}", exc_info=True)
        raise MCPError("READ_ERROR", f"Failed to read prompt file: {e!s}") from e
    else:
        return {"content": content}
