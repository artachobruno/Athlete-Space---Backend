# MCP Tool Inventory

This document catalogs all operations that the orchestrator agent and tools perform that need to be moved behind MCP servers.

## Overview

The system uses:
- **Orchestrator Agent**: Main conversational agent using pydantic_ai
- **9 Tools**: Coaching tools called by the orchestrator
- **Database Operations**: SQLAlchemy queries to SQLite/PostgreSQL
- **Filesystem Operations**: Reading prompt files
- **LLM Calls**: Via pydantic_ai (OpenAI API)

---

## 1. ORCHESTRATOR AGENT OPERATIONS

### Tool: `_load_orchestrator_prompt`
- **Current function name**: `_load_orchestrator_prompt()`
- **File path**: `app/coach/agents/orchestrator_agent.py:37-49`
- **Input params**: None
- **Output**: `str` (prompt content)
- **Side effects**: Reads file from filesystem
- **Operation type**: Filesystem read

### Tool: `load_context`
- **Current function name**: `load_context(athlete_id: int, limit: int = 20)`
- **File path**: `app/coach/utils/context_management.py:14-35`
- **Input params**:
  - `athlete_id`: int
  - `limit`: int (default: 20)
- **Output**: `list[dict[str, str]]` (conversation history)
- **Side effects**: Queries `CoachMessage` table
- **Operation type**: Database read

### Tool: `save_context`
- **Current function name**: `save_context(athlete_id: int, model_name: str, user_message: str, assistant_message: str)`
- **File path**: `app/coach/utils/context_management.py:38-78`
- **Input params**:
  - `athlete_id`: int
  - `model_name`: str
  - `user_message`: str
  - `assistant_message`: str
- **Output**: `None`
- **Side effects**: Inserts 2 rows into `CoachMessage` table
- **Operation type**: Database write

### Tool: `ORCHESTRATOR_AGENT.run`
- **Current function name**: `ORCHESTRATOR_AGENT.run(user_prompt, deps, message_history, usage_limits)`
- **File path**: `app/coach/agents/orchestrator_agent.py:232-237`
- **Input params**:
  - `user_prompt`: str
  - `deps`: CoachDeps
  - `message_history`: list[ModelMessage] | None
  - `usage_limits`: UsageLimits
- **Output**: `AgentResult[OrchestratorAgentResponse]`
- **Side effects**: Calls OpenAI API via pydantic_ai, may call tools
- **Operation type**: External API (LLM)

---

## 2. COACHING TOOLS

### Tool: `recommend_next_session`
- **Current function name**: `recommend_next_session(state: AthleteState, user_id: str | None)`
- **File path**: `app/coach/tools/next_session.py:211-247`
- **Input params**:
  - `state`: AthleteState
  - `user_id`: str | None
- **Output**: `str` (recommendation or clarification)
- **Side effects**:
  - Queries `Activity` table (via `_get_recent_activities`, `_get_yesterday_activities`)
  - Calls LLM via `CoachLLMClient.generate_daily_decision()`
- **Operation type**: Database read + External API (LLM)

### Tool: `add_workout`
- **Current function name**: `add_workout(state: AthleteState, workout_description: str, user_id: str | None, athlete_id: int | None)`
- **File path**: `app/coach/tools/add_workout.py:182-247`
- **Input params**:
  - `state`: AthleteState
  - `workout_description`: str
  - `user_id`: str | None
  - `athlete_id`: int | None
- **Output**: `str` (confirmation message)
- **Side effects**:
  - Inserts into `PlannedSession` table (via `save_planned_sessions`)
- **Operation type**: Database write

### Tool: `adjust_training_load`
- **Current function name**: `adjust_training_load(state: AthleteState, message: str)`
- **File path**: `app/coach/tools/adjust_load.py:6-22`
- **Input params**:
  - `state`: AthleteState
  - `message`: str
- **Output**: `str` (training load data)
- **Side effects**: None (pure computation)
- **Operation type**: Computation only

### Tool: `explain_training_state`
- **Current function name**: `explain_training_state(state: AthleteState)`
- **File path**: `app/coach/tools/explain_state.py:6-35`
- **Input params**:
  - `state`: AthleteState
- **Output**: `str` (state data)
- **Side effects**: None (pure computation)
- **Operation type**: Computation only

### Tool: `run_analysis`
- **Current function name**: `run_analysis(state: AthleteState)`
- **File path**: `app/coach/tools/run_analysis.py:6-33`
- **Input params**:
  - `state`: AthleteState
- **Output**: `str` (analysis data)
- **Side effects**: None (pure computation)
- **Operation type**: Computation only

### Tool: `share_report`
- **Current function name**: `share_report(state: AthleteState)`
- **File path**: `app/coach/tools/share_report.py:8-37`
- **Input params**:
  - `state`: AthleteState
- **Output**: `str` (report data)
- **Side effects**: None (pure computation)
- **Operation type**: Computation only

### Tool: `plan_week`
- **Current function name**: `plan_week(state: AthleteState)`
- **File path**: `app/coach/tools/plan_week.py:6-32`
- **Input params**:
  - `state`: AthleteState
- **Output**: `str` (planning data)
- **Side effects**: None (pure computation)
- **Operation type**: Computation only

### Tool: `plan_race_build`
- **Current function name**: `plan_race_build(message: str, user_id: str | None, athlete_id: int | None)`
- **File path**: `app/coach/tools/plan_race.py:251-316`
- **Input params**:
  - `message`: str
  - `user_id`: str | None
  - `athlete_id`: int | None
- **Output**: `str` (plan details or clarification)
- **Side effects**:
  - Inserts multiple rows into `PlannedSession` table (via `save_planned_sessions`)
  - Generates sessions via `generate_race_build_sessions()`
- **Operation type**: Database write

### Tool: `plan_season`
- **Current function name**: `plan_season(message: str, user_id: str | None, athlete_id: int | None)`
- **File path**: `app/coach/tools/plan_season.py:84-147`
- **Input params**:
  - `message`: str
  - `user_id`: str | None
  - `athlete_id`: int | None
- **Output**: `str` (plan details)
- **Side effects**:
  - Inserts multiple rows into `PlannedSession` table (via `save_planned_sessions`)
  - Generates sessions via `generate_season_sessions()`
- **Operation type**: Database write

---

## 3. DATABASE OPERATIONS

### Tool: `_get_recent_activities`
- **Current function name**: `_get_recent_activities(user_id: str, days: int = 7)`
- **File path**: `app/coach/tools/next_session.py:13-37`
- **Input params**:
  - `user_id`: str
  - `days`: int (default: 7)
- **Output**: `list[Activity]`
- **Side effects**: Queries `Activity` table with filters
- **Operation type**: Database read

### Tool: `_get_yesterday_activities`
- **Current function name**: `_get_yesterday_activities(user_id: str)`
- **File path**: `app/coach/tools/next_session.py:40-67`
- **Input params**:
  - `user_id`: str
- **Output**: `list[Activity]`
- **Side effects**: Queries `Activity` table with date filters
- **Operation type**: Database read

### Tool: `save_planned_sessions`
- **Current function name**: `save_planned_sessions(user_id: str, athlete_id: int, sessions: list[dict], plan_type: str, plan_id: str | None)`
- **File path**: `app/coach/tools/session_planner.py:13-95`
- **Input params**:
  - `user_id`: str
  - `athlete_id`: int
  - `sessions`: list[dict]
  - `plan_type`: str
  - `plan_id`: str | None
- **Output**: `int` (number of sessions saved)
- **Side effects**:
  - Queries `PlannedSession` table (check for duplicates)
  - Inserts rows into `PlannedSession` table
- **Operation type**: Database read + write

---

## 4. FILESYSTEM OPERATIONS

### Tool: `_load_prompt`
- **Current function name**: `_load_prompt(filename: str)`
- **File path**: `app/coach/utils/llm_client.py:49-64`
- **Input params**:
  - `filename`: str (e.g., "season_plan.txt", "daily_decision.txt")
- **Output**: `str` (prompt content)
- **Side effects**: Reads file from `app/coach/prompts/` directory
- **Operation type**: Filesystem read

---

## 5. LLM OPERATIONS (via CoachLLMClient)

### Tool: `generate_daily_decision`
- **Current function name**: `CoachLLMClient.generate_daily_decision(context: dict[str, Any])`
- **File path**: `app/coach/utils/llm_client.py:220-280`
- **Input params**:
  - `context`: dict[str, Any]
- **Output**: `DailyDecision` (Pydantic model)
- **Side effects**:
  - Reads prompt file from filesystem
  - Calls OpenAI API via pydantic_ai
- **Operation type**: Filesystem read + External API (LLM)

### Tool: `generate_season_plan`
- **Current function name**: `CoachLLMClient.generate_season_plan(context: dict[str, Any])`
- **File path**: `app/coach/utils/llm_client.py:93-152`
- **Input params**:
  - `context`: dict[str, Any]
- **Output**: `SeasonPlan` (Pydantic model)
- **Side effects**:
  - Reads prompt file from filesystem
  - Calls OpenAI API via pydantic_ai
- **Operation type**: Filesystem read + External API (LLM)

### Tool: `generate_weekly_intent`
- **Current function name**: `CoachLLMClient.generate_weekly_intent(context: dict[str, Any], previous_volume: float | None)`
- **File path**: `app/coach/utils/llm_client.py:154-218`
- **Input params**:
  - `context`: dict[str, Any]
  - `previous_volume`: float | None
- **Output**: `WeeklyIntent` (Pydantic model)
- **Side effects**:
  - Reads prompt file from filesystem
  - Calls OpenAI API via pydantic_ai
- **Operation type**: Filesystem read + External API (LLM)

### Tool: `generate_weekly_report`
- **Current function name**: `CoachLLMClient.generate_weekly_report(context: dict[str, Any])`
- **File path**: `app/coach/utils/llm_client.py:282-340`
- **Input params**:
  - `context`: dict[str, Any]
- **Output**: `WeeklyReport` (Pydantic model)
- **Side effects**:
  - Reads prompt file from filesystem
  - Calls OpenAI API via pydantic_ai
- **Operation type**: Filesystem read + External API (LLM)

---

## 6. SESSION PLANNER OPERATIONS

### Tool: `generate_race_build_sessions`
- **Current function name**: `generate_race_build_sessions(race_date: datetime, race_distance: str, target_time: str | None, start_date: datetime | None)`
- **File path**: `app/coach/tools/session_planner.py:162-223`
- **Input params**:
  - `race_date`: datetime
  - `race_distance`: str
  - `target_time`: str | None
  - `start_date`: datetime | None
- **Output**: `list[dict]` (session dictionaries)
- **Side effects**: None (pure computation)
- **Operation type**: Computation only

### Tool: `generate_season_sessions`
- **Current function name**: `generate_season_sessions(season_start: datetime, season_end: datetime, target_races: list[dict] | None)`
- **File path**: `app/coach/tools/session_planner.py:226-380`
- **Input params**:
  - `season_start`: datetime
  - `season_end`: datetime
  - `target_races`: list[dict] | None
- **Output**: `list[dict]` (session dictionaries)
- **Side effects**: None (pure computation)
- **Operation type**: Computation only

---

## SUMMARY BY OPERATION TYPE

### Database Reads (5)
1. `load_context` - Read CoachMessage
2. `_get_recent_activities` - Read Activity
3. `_get_yesterday_activities` - Read Activity
4. `save_planned_sessions` - Read PlannedSession (duplicate check)

### Database Writes (4)
1. `save_context` - Write CoachMessage (2 rows)
2. `add_workout` - Write PlannedSession
3. `plan_race_build` - Write PlannedSession (multiple rows)
4. `plan_season` - Write PlannedSession (multiple rows)

### Filesystem Reads (2)
1. `_load_orchestrator_prompt` - Read orchestrator.txt
2. `_load_prompt` - Read various prompt files (season_plan.txt, daily_decision.txt, weekly_intent.txt, weekly_report.txt)

### External API Calls - LLM (5)
1. `ORCHESTRATOR_AGENT.run` - OpenAI via pydantic_ai
2. `generate_daily_decision` - OpenAI via pydantic_ai
3. `generate_season_plan` - OpenAI via pydantic_ai
4. `generate_weekly_intent` - OpenAI via pydantic_ai
5. `generate_weekly_report` - OpenAI via pydantic_ai

### Pure Computation (6)
1. `adjust_training_load` - Format state data
2. `explain_training_state` - Format state data
3. `run_analysis` - Format state data
4. `share_report` - Format state data
5. `plan_week` - Format state data
6. `generate_race_build_sessions` - Generate session list
7. `generate_season_sessions` - Generate session list

---

## MCP SERVER GROUPING RECOMMENDATIONS

### DB Server (`mcp-db-server`)
- All database read/write operations
- Tools: `load_context`, `save_context`, `_get_recent_activities`, `_get_yesterday_activities`, `save_planned_sessions`

### FS Server (`mcp-fs-server`)
- All filesystem read operations
- Tools: `_load_orchestrator_prompt`, `_load_prompt`

### LLM Server (`mcp-llm-server`)
- All LLM API calls
- Tools: `ORCHESTRATOR_AGENT.run`, `generate_daily_decision`, `generate_season_plan`, `generate_weekly_intent`, `generate_weekly_report`

### Code Server (`mcp-code-server`)
- Pure computation operations (can stay in orchestrator or move to MCP)
- Tools: `adjust_training_load`, `explain_training_state`, `run_analysis`, `share_report`, `plan_week`, `generate_race_build_sessions`, `generate_season_sessions`

---

## NOTES

- **No vector search operations** found in the codebase
- **No subprocess/exec operations** found in coach code
- **No direct HTTP calls** in coach code (Strava API calls are in `app/integrations/strava/`)
- All LLM calls go through pydantic_ai, which uses OpenAI API
- Database operations use SQLAlchemy with `get_session()` context manager
- Filesystem operations use `pathlib.Path` for reading prompt files
