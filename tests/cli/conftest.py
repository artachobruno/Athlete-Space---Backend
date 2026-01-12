"""Shared fixtures for CLI tests.

This conftest makes fixtures from tests/mcp/conftest.py available
to CLI tests via pytest_plugins.
"""

import pytest

# Make MCP fixtures available to CLI tests
pytest_plugins = ["tests.mcp.conftest"]
