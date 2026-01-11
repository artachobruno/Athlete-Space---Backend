"""Unified prompt loader interface.

This module provides a single interface for loading prompts that works in both
local and deployed environments:
- Local (APP_ENV=local): Reads directly from filesystem
- Staging/Production (APP_ENV=staging|production): Uses MCP FS server

All prompt loading should go through load_prompt() - never access prompts directly.
"""

import os
from pathlib import Path

from loguru import logger

from app.coach.mcp_client import MCPError, call_tool

APP_ENV = os.getenv("APP_ENV", "local")


def _load_prompt_local(name: str) -> str:
    """Load prompt from local filesystem (CLI/dev only).

    Args:
        name: Prompt filename (e.g., "orchestrator.txt", "season_plan.txt")

    Returns:
        Prompt content as string

    Raises:
        RuntimeError: If called outside local environment
        FileNotFoundError: If prompt file doesn't exist
    """
    if APP_ENV != "local":
        raise RuntimeError("Local prompt loading forbidden outside local env")

    prompt_dir = Path(__file__).parent
    prompt_path = prompt_dir / name

    if not prompt_path.exists():
        raise FileNotFoundError(f"Local prompt not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


async def _load_prompt_remote(name: str) -> str:
    """Load prompt via MCP FS server (staging/production).

    Args:
        name: Prompt filename (e.g., "orchestrator.txt", "season_plan.txt")

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
        RuntimeError: If MCP call fails
    """
    # Handle special case: orchestrator.txt uses load_orchestrator_prompt tool
    if name == "orchestrator.txt":
        try:
            result = await call_tool("load_orchestrator_prompt", {})
            return result["content"]
        except MCPError as e:
            if e.code == "FILE_NOT_FOUND":
                raise FileNotFoundError(f"Prompt file not found: {name}") from e
            raise RuntimeError(f"Failed to load orchestrator prompt: {e.message}") from e

    # Generic prompt loading via load_prompt tool (works for all other prompts)
    try:
        result = await call_tool("load_prompt", {"filename": name})
        return result["content"]
    except MCPError as e:
        if e.code == "FILE_NOT_FOUND":
            raise FileNotFoundError(f"Prompt file not found: {name}") from e
        raise RuntimeError(f"Failed to load prompt: {e.message}") from e


async def load_prompt(name: str) -> str:
    """Load a prompt file (unified interface).

    This is the ONLY function that should be used to load prompts.
    It automatically selects the appropriate loading method based on APP_ENV:
    - local: Reads from filesystem
    - staging/production: Uses MCP FS server

    Args:
        name: Prompt filename (e.g., "orchestrator.txt", "season_plan.txt")

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
        RuntimeError: If loading fails

    Examples:
        >>> prompt = await load_prompt("season_plan.txt")
        >>> orchestrator_prompt = await load_prompt("orchestrator.txt")
    """
    mode = "local" if APP_ENV == "local" else "remote"
    logger.info(f"Loading prompt '{name}' via {mode}")

    if APP_ENV == "local":
        return _load_prompt_local(name)
    return await _load_prompt_remote(name)
