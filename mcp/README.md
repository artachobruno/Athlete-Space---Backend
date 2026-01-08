# MCP Servers Implementation

This directory contains MCP-compliant HTTP servers for database and filesystem operations.

## Structure

```
mcp/
├── contracts/          # Tool contracts (JSON schemas)
├── db_server/          # Database operations server
│   ├── main.py        # FastAPI server entry point
│   ├── errors.py      # MCP error classes
│   └── tools/         # Tool implementations
│       ├── context.py      # load_context, save_context
│       ├── activities.py  # get_recent_activities, get_yesterday_activities
│       └── sessions.py     # save_planned_sessions, add_workout, plan_race_build, plan_season
└── fs_server/          # Filesystem operations server
    ├── main.py        # FastAPI server entry point
    ├── errors.py      # MCP error classes
    └── tools/         # Tool implementations
        └── prompts.py     # load_orchestrator_prompt, load_prompt
```

## Servers

### MCP DB Server (`mcp-db-server`)

**Port:** 8080
**Endpoint:** `POST /mcp/tools/call`

**Tools:**
- `load_context` - Load conversation history
- `save_context` - Save conversation messages
- `get_recent_activities` - Get recent activities
- `get_yesterday_activities` - Get yesterday's activities
- `save_planned_sessions` - Save planned training sessions
- `add_workout` - Add workout to calendar
- `plan_race_build` - Plan race build and save sessions
- `plan_season` - Plan season and save sessions

### MCP FS Server (`mcp-fs-server`)

**Port:** 8081
**Endpoint:** `POST /mcp/tools/call`

**Tools:**
- `load_orchestrator_prompt` - Load orchestrator prompt file
- `load_prompt` - Load prompt file by filename

## Running Locally

### DB Server
```bash
cd mcp/db_server
python main.py
# Server runs on http://0.0.0.0:8080
```

### FS Server
```bash
cd mcp/fs_server
python main.py
# Server runs on http://0.0.0.0:8081
```

## API Usage

### Request Format
```json
{
  "tool": "tool_name",
  "arguments": {
    "param1": "value1",
    "param2": "value2"
  }
}
```

### Success Response
```json
{
  "result": {
    // Tool-specific result data
  }
}
```

### Error Response
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Error description"
  }
}
```

## Error Codes

All error codes match the contract specifications:
- `ATHLETE_NOT_FOUND`
- `USER_NOT_FOUND`
- `DB_ERROR`
- `INVALID_INPUT`
- `INVALID_LIMIT`
- `INVALID_DAYS`
- `INVALID_SESSION_DATA`
- `INVALID_DATE_FORMAT`
- `INVALID_WORKOUT_DESCRIPTION`
- `MISSING_RACE_INFO`
- `INVALID_RACE_DATE`
- `MISSING_SEASON_INFO`
- `INVALID_SEASON_DATES`
- `INVALID_MESSAGE`
- `FILE_NOT_FOUND`
- `READ_ERROR`
- `ENCODING_ERROR`
- `INVALID_FILENAME`

## Notes

- All tools validate inputs against contract schemas
- All errors return structured MCP-compliant responses
- Database operations use existing SQLAlchemy session logic
- Filesystem operations are restricted to prompts directory
- No LLM calls in MCP servers (LLM remains in orchestrator)
