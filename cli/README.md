# Virtus MCP CLI – Local / Offline Orchestrator Runner

This CLI allows you to run the **exact same orchestrator code path used in production**, locally and offline, while enforcing **MCP-only access** for all database and filesystem operations.

There are **two required MCP servers** and **one CLI entrypoint**.

---

## Architecture Overview

```
CLI
 └── Orchestrator (pure logic)
      └── MCP Client
           ├── MCP DB Server (context, activities, sessions)
           └── MCP FS Server (prompt loading)
```

**Important guarantees:**

* ❌ No direct DB access in the orchestrator
* ❌ No direct filesystem access in the orchestrator
* ✅ All side effects go through MCP
* ✅ Local behavior = Production behavior

---

## Prerequisites

* Python 3.11+
* Virtual environment activated
* Dependencies installed:

```bash
pip install -r requirements.txt
```

---

## Quick Start (Automated Setup)

For macOS users, you can use the automated setup script to launch all three terminals at once:

```bash
# Option 1: Python script (recommended)
python cli/run_cli_setup.py

# Option 2: Shell script
./cli/run_cli_setup.sh
```

This will automatically:
1. ✅ Start MCP DB Server in Terminal 1
2. ✅ Start MCP FS Server in Terminal 2
3. ✅ Start CLI Client in Terminal 3 (with environment variables set)

The script will wait a few seconds for servers to start, verify MCP connectivity, and then launch the interactive CLI.

**Note:** On non-macOS systems, the script will print instructions for running each component manually.

---

## Step 1: Start MCP Servers (REQUIRED)

The CLI **will not run** unless both MCP servers are running.

### Terminal 1 — MCP Database Server

```bash
python mcp/db_server/main.py
```

* Runs on: `http://localhost:8080`
* Handles:

  * Conversation context (load/save)
  * Activity queries
  * Planned session persistence

---

### Terminal 2 — MCP Filesystem Server

```bash
python mcp/fs_server/main.py
```

* Runs on: `http://localhost:8081`
* Handles:

  * Orchestrator prompt loading
  * Tool prompt loading

---

## Step 2: Set Environment Variables

In the terminal where you will run the CLI:

```bash
export MCP_DB_SERVER_URL=http://localhost:8080
export MCP_FS_SERVER_URL=http://localhost:8081
```

Optional (but recommended for LLM calls):

```bash
export OPENAI_API_KEY=your_key_here
```

---

## Step 3: Verify MCP Servers

Before running the orchestrator, verify MCP connectivity:

```bash
python cli/cli.py check-mcp
```

### Expected Output

* ✅ Green confirmation panel
* Both DB and FS servers reachable

If this fails, **do not proceed** — the orchestrator is intentionally blocked without MCP.

---

## Step 4: Run the CLI

### One-Shot Mode (single input)

```bash
python cli/cli.py client -i "hello"
```

Example:

```bash
python cli/cli.py client -i "What should my training look like this week?"
```

---

### Interactive Mode

```bash
python cli/cli.py client
```

You will see:

```
Virtus AI Orchestrator CLI - Interactive Mode
Enter your message (empty line, EXIT, or QUIT to exit)
```

Type messages interactively.
Exit with:

* empty input
* `EXIT`
* `QUIT`
* `Ctrl+C`

---

### Optional Flags

```bash
--athlete-id <int>     # Default: 1
--user-id <string>     # Default: cli-user
--days <int>           # Training history window (default: 60)
--days-to-race <int>   # Optional race horizon
--output <file.json>   # Write response to file
--debug                # Enable debug logging
--no-pretty            # Disable pretty JSON output
```

Example:

```bash
python cli/cli.py client \
  -i "Plan my next week" \
  --athlete-id 1 \
  --days 90 \
  --output output.json \
  --debug
```

---

## Expected Behavior

When everything is wired correctly, you will see logs like:

```
MCP servers reachable
Running orchestrator
Starting conversation
Calling orchestrator LLM
Conversation completed
```

If MCP DB operations fail (e.g., missing tables), you may see:

```
Failed to load context: DB_ERROR
Failed to save context: DB_ERROR
```

This is **acceptable and expected** during early local setup and proves:

* MCP is enforced
* Failures are isolated
* Orchestrator remains functional

---

## Common Errors & Fixes

### ❌ "Missing MCP server URLs"

You forgot to export:

```bash
export MCP_DB_SERVER_URL=http://localhost:8080
export MCP_FS_SERVER_URL=http://localhost:8081
```

---

### ❌ "MCP DB_ERROR"

Likely causes:

* Context table not migrated yet
* SQLite schema mismatch

This is a **data-layer issue**, not an MCP or orchestrator issue.

---

## Why This CLI Exists

This CLI is intentionally strict.

It exists to ensure:

* MCP is not optional
* Local dev cannot diverge from production
* Side effects are observable, mockable, and replaceable
* Future Cloud Run deployment is trivial

If the CLI works, **production will work**.

---

## Summary

To run the system locally:

1. ✅ Start **MCP DB server**
2. ✅ Start **MCP FS server**
3. ✅ Export MCP environment variables
4. ✅ Run the CLI

There are **no shortcuts** — by design.

---

If you want, next we can:

* Add this to CI as an end-to-end MCP test
* Add automatic DB migrations to MCP
* Prepare Cloud Run deployment manifests
* Lock MCP enforcement with a runtime assertion

Just tell me the next step.
