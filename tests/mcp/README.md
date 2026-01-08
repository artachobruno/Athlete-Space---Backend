# Running MCP Tests

This guide explains how to run the MCP-aware smoke tests that verify tool routing and MCP enforcement.

## Prerequisites

1. **MCP Servers Running**: The tests require both MCP servers to be running
2. **Environment Variables**: Set `MCP_DB_SERVER_URL` and `MCP_FS_SERVER_URL`
3. **Database**: A test database with the required schema
4. **OpenAI API Key**: Required for LLM calls (set via `OPENAI_API_KEY`)

## Step 1: Start MCP Servers

You need to run both MCP servers in separate terminals:

### Terminal 1: DB Server
```bash
cd mcp/db_server
python main.py
```
Server runs on `http://localhost:8080`

### Terminal 2: FS Server
```bash
cd mcp/fs_server
python main.py
```
Server runs on `http://localhost:8081`

## Step 2: Set Environment Variables

```bash
export MCP_DB_SERVER_URL=http://localhost:8080
export MCP_FS_SERVER_URL=http://localhost:8081
export OPENAI_API_KEY=sk-your-key-here
```

Or create a `.env` file in the project root:
```bash
MCP_DB_SERVER_URL=http://localhost:8080
MCP_FS_SERVER_URL=http://localhost:8081
OPENAI_API_KEY=sk-your-key-here
```

## Step 3: Run Tests

### Run All MCP Tests
```bash
pytest tests/mcp/
```

### Run Specific Test Files

**Tool routing tests:**
```bash
pytest tests/mcp/test_tool_routing.py -v
```

**Hard MCP enforcement tests:**
```bash
pytest tests/mcp/test_mcp_required.py -v
```

**Original smoke tests:**
```bash
pytest tests/mcp/test_mcp_smoke.py -v
```

### Run Specific Tests

```bash
# Test that greeting doesn't hit DB
pytest tests/mcp/test_tool_routing.py::test_greeting_does_not_hit_db -v

# Test that recommend_next_session calls get_recent_activities
pytest tests/mcp/test_tool_routing.py::test_calls_get_recent_activities -v

# Test MCP enforcement (fails without MCP)
pytest tests/mcp/test_mcp_required.py::test_orchestrator_fails_without_both_mcp_servers -v
```

## Test Output

### Successful Test
```
tests/mcp/test_tool_routing.py::test_calls_get_recent_activities PASSED
tests/mcp/test_tool_routing.py::test_greeting_does_not_hit_db PASSED
```

### Failed Test (Wrong Tool Called)
```
FAILED tests/mcp/test_tool_routing.py::test_greeting_does_not_hit_db
AssertionError: Greeting should not call data query/write tools, but called: {'get_recent_activities'}
```

### Skipped Test (MCP Servers Not Running)
```
SKIPPED [1] tests/mcp/conftest.py:27: MCP tests skipped, missing env vars: ['MCP_DB_SERVER_URL']
```

## What the Tests Verify

### Tool Routing Tests (`test_tool_routing.py`)
- ✅ Verifies correct MCP tools are called for specific user inputs
- ✅ Ensures greetings don't trigger unnecessary DB queries
- ✅ Validates tool routing hasn't regressed

### Hard Enforcement Tests (`test_mcp_required.py`)
- ✅ Ensures orchestrator fails when MCP servers are unavailable
- ✅ Prevents silent fallback to direct DB/FS access
- ✅ Guarantees MCP is never bypassed

### Smoke Tests (`test_mcp_smoke.py`)
- ✅ End-to-end tests of orchestrator functionality
- ✅ Verifies MCP integration works correctly
- ✅ Tests various conversation flows

## Troubleshooting

### Tests Skip with "missing env vars"
**Solution**: Set `MCP_DB_SERVER_URL` and `MCP_FS_SERVER_URL` environment variables.

### Tests Fail with Connection Errors
**Solution**: Ensure both MCP servers are running:
- DB server on port 8080
- FS server on port 8081

### Tests Fail with "Tool not found"
**Solution**: Check that MCP servers are running the latest version with all required tools.

### Tests Timeout
**Solution**:
- Check OpenAI API key is set correctly
- Verify network connectivity
- Increase timeout in test file if needed (default: 30 seconds)

## Running Tests in CI/CD

For CI/CD pipelines, you'll need to:
1. Start MCP servers as background services
2. Set environment variables
3. Run tests with appropriate timeouts

Example GitHub Actions workflow snippet:
```yaml
- name: Start MCP DB Server
  run: |
    cd mcp/db_server
    python main.py &
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}

- name: Start MCP FS Server
  run: |
    cd mcp/fs_server
    python main.py &

- name: Run MCP Tests
  run: pytest tests/mcp/ -v
  env:
    MCP_DB_SERVER_URL: http://localhost:8080
    MCP_FS_SERVER_URL: http://localhost:8081
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```
