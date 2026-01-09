# Running B8 End-to-End Validation Tests

## Prerequisites

1. **Database must be running** - The tests need database access to:
   - Create planned sessions
   - Check for existing sessions
   - Run reconciliation

2. **MCP Servers should be running** (optional but recommended):
   - MCP DB Server (port 8080) - for tool calls
   - MCP FS Server (port 8081) - for prompt loading

3. **Environment variables** - Make sure your `.env` or environment has:
   - Database connection string
   - Any required API keys

## Quick Start

### Option 1: Run directly with Python

```bash
cd /Users/bruno/Desktop/AI/AthleteSpace/Athlete-Space---Backend
python scripts/validate_b8_end_to_end.py
```

### Option 2: Run with virtual environment

```bash
# Activate your virtual environment first
source venv/bin/activate  # or: conda activate virtus-ai

# Then run
python scripts/validate_b8_end_to_end.py
```

### Option 3: Run with Python module syntax

```bash
python -m scripts.validate_b8_end_to_end
```

## What the Tests Do

The script runs 6 validation tests:

1. **Test 1: Basic Weekly Planning** - Tests planning without constraints
2. **Test 2: Planning with Fatigue Feedback** - Tests B17 + B18 + B8 integration
3. **Test 3: Forced Rest Days** - Tests rest day enforcement
4. **Test 4: Calendar Visibility** - Tests sessions appear in calendar
5. **Test 5: Reconciliation** - Tests B12 reconciliation works
6. **Test 6: Safety Enforcement** - Tests bounds are respected

## Expected Output

You should see:
- Log messages for each test
- ✅ PASSED or ❌ FAILED for each test
- Final summary with all test results

## Troubleshooting

### Database Connection Errors

If you see database errors:
- Make sure your database is running
- Check your database connection string in `.env`
- Verify the database schema is up to date

### MCP Server Errors

If you see MCP errors:
- The tests will try to use MCP but can fall back to direct calls
- MCP servers are optional for these tests
- If needed, start MCP servers:
  ```bash
  # Terminal 1: DB Server
  python -m mcp.db_server.main

  # Terminal 2: FS Server
  python -m mcp.fs_server.main
  ```

### Import Errors

If you see import errors:
- Make sure you're in the project root directory
- Check that all dependencies are installed: `pip install -r requirements.txt`
- Verify Python path includes the project root

## Test Data

The tests use:
- `user_id = "test_user_123"`
- `athlete_id = 1`

**Note:** The tests will create real planned sessions in your database. You may want to clean them up after testing.

## Cleaning Up Test Data

After running tests, you can clean up test sessions:

```python
from app.db.session import get_session
from app.db.models import PlannedSession
from sqlalchemy import select

with get_session() as session:
    test_sessions = session.execute(
        select(PlannedSession).where(
            PlannedSession.user_id == "test_user_123"
        )
    ).scalars().all()

    for session_obj in test_sessions:
        session.delete(session_obj)
    session.commit()
```
