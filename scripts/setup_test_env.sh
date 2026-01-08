#!/bin/bash
# Setup test environment for async tests

set -e

echo "Setting up test environment for async tests..."

# Check if we're using uv or pip
if command -v uv &> /dev/null; then
    echo "Using uv to install test dependencies..."
    uv sync --group test
elif command -v pip &> /dev/null; then
    echo "Using pip to install test dependencies..."
    pip install -e ".[test]"
else
    echo "Error: Neither uv nor pip found. Please install one of them."
    exit 1
fi

echo ""
echo "✅ Test dependencies installed!"
echo ""
echo "To verify pytest-asyncio is installed, run:"
echo "  python -c 'import pytest_asyncio; print(f\"pytest-asyncio version: {pytest_asyncio.__version__}\")'"
echo ""
echo "To run the tests:"
echo "  export MCP_DB_SERVER_URL=http://localhost:8080"
echo "  export MCP_FS_SERVER_URL=http://localhost:8081"
echo "  pytest tests/mcp/test_mcp_smoke.py -v"
