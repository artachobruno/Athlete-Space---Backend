# Step 4 Summary - Orchestrator → MCP Client Wiring

## Changes Made

### ✅ Created MCP Client
- **File**: `app/coach/mcp_client.py`
- **Features**:
  - HTTP client for MCP server communication
  - Tool routing table (DB server: 8080, FS server: 8081)
  - Error handling with MCPError exceptions
  - Timeout enforcement (30s)
  - Structured error responses

### ✅ Updated Orchestrator Agent
- **File**: `app/coach/agents/orchestrator_agent.py`
- **Changes**:
  - Removed: `from app.coach.utils.context_management import load_context, save_context`
  - Removed: Direct filesystem read (`_load_orchestrator_prompt`)
  - Added: `from app.coach.mcp_client import MCPError, call_tool`
  - `load_context()` → `await call_tool("load_context", ...)`
  - `save_context()` → `await call_tool("save_context", ...)`
  - `_load_orchestrator_prompt()` → `await call_tool("load_orchestrator_prompt", ...)`
  - Agent initialization now loads instructions asynchronously via MCP

### ✅ Updated Tools

#### `app/coach/tools/next_session.py`
- Removed: `from app.db.models import Activity`, `from app.db.session import get_session`
- Removed: `from sqlalchemy import select`
- Added: `from app.coach.mcp_client import MCPError, call_tool`
- `_get_recent_activities()` → async, uses `call_tool("get_recent_activities", ...)`
- `_get_yesterday_activities()` → async, uses `call_tool("get_yesterday_activities", ...)`
- `recommend_next_session()` → async
- Updated activity formatting to work with dicts instead of ORM objects

#### `app/coach/tools/session_planner.py`
- Removed: `from app.db.models import PlannedSession`, `from app.db.session import get_session`
- Removed: `from sqlalchemy import select`
- Added: `from app.coach.mcp_client import MCPError, call_tool`
- `save_planned_sessions()` → async, uses `call_tool("save_planned_sessions", ...)`
- Converts datetime objects to ISO strings for MCP

#### `app/coach/tools/add_workout.py`
- `add_workout()` → async
- `save_planned_sessions()` calls now use `await`

#### `app/coach/tools/plan_race.py`
- `plan_race_build()` → async
- `_create_and_save_plan()` → async
- `save_planned_sessions()` calls now use `await`

#### `app/coach/tools/plan_season.py`
- `plan_season()` → async
- `save_planned_sessions()` calls now use `await`

#### `app/coach/utils/llm_client.py`
- `_load_prompt()` → async, uses `call_tool("load_prompt", ...)`
- `generate_season_plan()` → async
- `generate_weekly_intent()` → async
- `generate_daily_decision()` → async
- `generate_weekly_report()` → async
- All prompt loading now goes through MCP

### ✅ Updated Orchestrator Tool Wrappers
- Removed `asyncio.to_thread()` for tools that are now async
- `recommend_next_session_tool` → direct async call
- `add_workout_tool` → direct async call
- `plan_race_build_tool` → direct async call
- `plan_season_tool` → direct async call

## Verification

### ✅ No Direct DB/FS Imports in Orchestrator
- `app/coach/agents/orchestrator_agent.py`: ✅ No DB/FS imports
- `app/coach/tools/next_session.py`: ✅ No DB imports
- `app/coach/tools/session_planner.py`: ✅ No DB imports
- `app/coach/utils/llm_client.py`: ✅ No filesystem imports

### ✅ All Side Effects Go Through MCP
- Context loading: ✅ MCP
- Context saving: ✅ MCP
- Activity queries: ✅ MCP
- Session saving: ✅ MCP
- Prompt loading: ✅ MCP

### ✅ Error Handling
- MCP errors properly caught and logged
- Errors propagate to agent (not swallowed)
- Graceful degradation where appropriate (e.g., empty history on load failure)

## Environment Variables

The MCP client uses these environment variables (with defaults):
- `MCP_DB_SERVER_URL` (default: `http://localhost:8080`)
- `MCP_FS_SERVER_URL` (default: `http://localhost:8081`)

## Next Steps

1. **Deploy MCP servers** to Cloud Run
2. **Update environment variables** in orchestrator deployment
3. **Test end-to-end** with MCP servers running
4. **Monitor** MCP call latency and errors

## Notes

- All tools that were sync are now async to support MCP calls
- The orchestrator agent initialization is now lazy (loads instructions on first conversation)
- Activity objects are now dictionaries (from MCP responses)
- Date/datetime objects are converted to ISO strings for MCP transport
