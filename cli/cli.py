"""CLI for MCP-Wired Orchestrator.

Developer CLI to run the system offline/locally that exercises the same
orchestrator code path as production using MCP clients for all DB and filesystem operations.
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import typer
import uvicorn
from loguru import logger
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.text import Text

try:
    import redis
except ImportError:
    redis = None

try:
    from sqlalchemy import text
except ImportError:
    text = None

# Bootstrap must be imported after standard library imports
# but before app imports to set up sys.path correctly
# Handle both direct execution (python cli/cli.py) and module execution (python -m cli.cli)
try:
    import cli.bootstrap
except ImportError:
    # When running directly, add project root to sys.path
    _project_root = Path(__file__).parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

from sqlalchemy import select

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.services.chat_service import process_coach_chat
from app.config.settings import settings
from app.db.models import AthleteProfile, StravaAccount, User
from app.db.session import get_session

# Initialize Rich console for output
console = Console()

# Initialize Typer app
app = typer.Typer(
    name="virtus-cli",
    help="Virtus AI CLI - Offline/Local Orchestrator Testing",
    add_completion=False,
)

# CLI configuration
DEFAULT_ATHLETE_ID = 1
DEFAULT_USER_ID = "cli-user"
DEFAULT_DAYS = 60
DEFAULT_HOST = os.getenv("SERVER_HOST", "127.0.0.1")


@dataclass
class ClientConfig:
    """Configuration for client command."""

    input_text: str | None
    athlete_id: int
    user_id: str
    days: int
    days_to_race: int | None
    output_file: str | None
    pretty: bool


def _setup_logging(debug: bool = False) -> None:
    """Set up logging with console and file output.

    Args:
        debug: Enable debug logging level
    """
    # Remove default handler
    logger.remove()

    # Set log level
    log_level = "DEBUG" if debug else "INFO"

    # Console handler with color
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level=log_level,
        colorize=True,
    )

    # File handler with rotation
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"cli_{timestamp}.log"

    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level=log_level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
    )

    logger.info(f"Logging initialized (level={log_level}, file={log_file})")


def _validate_mcp_servers() -> None:
    """Validate that MCP servers are configured and reachable.

    Raises:
        RuntimeError: If MCP servers are not configured or not reachable
    """
    db_url = settings.mcp_db_server_url
    fs_url = settings.mcp_fs_server_url

    if not db_url or not fs_url:
        raise RuntimeError(
            "Missing MCP server URLs.\nSet:\n  MCP_DB_SERVER_URL=http://localhost:8080\n  MCP_FS_SERVER_URL=http://localhost:8081"
        )

    # Actively probe servers to verify they're reachable
    for name, url in [("DB", db_url), ("FS", fs_url)]:
        try:
            response = httpx.get(f"{url}/health", timeout=2.0)
            response.raise_for_status()
        except httpx.RequestError as e:
            raise RuntimeError(f"MCP {name} server not reachable at {url}. Make sure the server is running: {e}") from e
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"MCP {name} server at {url} returned error: {e.response.status_code}") from e

    logger.info(f"MCP servers reachable: DB={db_url}, FS={fs_url}")


def _write_file_sync(file_path: Path, content: str) -> None:
    """Write file synchronously (acceptable for CLI)."""
    file_path.write_text(content, encoding="utf-8")


def _format_response(response: dict, pretty: bool = True) -> str:
    """Format orchestrator response for output.

    Args:
        response: Orchestrator response dictionary
        pretty: Whether to pretty-print JSON

    Returns:
        Formatted response string
    """
    if pretty:
        return json.dumps(response, indent=2, ensure_ascii=False)
    return json.dumps(response, ensure_ascii=False)


@app.command()
def server(
    host: str = typer.Option(DEFAULT_HOST, "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind to"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
) -> None:
    """Run the FastAPI server.

    This command starts the existing FastAPI application using uvicorn.
    """
    logger.info(f"Starting FastAPI server on {host}:{port} (reload={reload})")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


@app.command()
def check_mcp() -> None:
    """Verify MCP servers are running and reachable.

    Checks that both MCP DB and FS servers are configured and responding.
    """
    try:
        _validate_mcp_servers()
    except RuntimeError as e:
        console.print(
            Panel(
                Text("MCP servers are NOT running", style="bold red"),
                subtitle=str(e),
                border_style="red",
            )
        )
        console.print("\n[yellow]To start MCP servers:[/yellow]")
        console.print("  Terminal 1: python mcp/db_server/main.py")
        console.print("  Terminal 2: python mcp/fs_server/main.py")
        console.print("\n[yellow]Then set environment variables:[/yellow]")
        console.print("  export MCP_DB_SERVER_URL=http://localhost:8080")
        console.print("  export MCP_FS_SERVER_URL=http://localhost:8081")
        raise typer.Exit(1) from e

    console.print(
        Panel(
            Text("MCP servers are running", style="bold green"),
            subtitle="DB and FS servers reachable",
            border_style="green",
        )
    )


@app.command()
def check_db() -> None:
    """Verify Redis and PostgreSQL connections are working.

    Checks that both Redis and PostgreSQL databases are configured and accessible.
    """
    results: list[tuple[str, bool, str]] = []

    # Check Redis
    try:
        if redis is None:
            results.append(("Redis", False, "redis package not installed (pip install redis)"))
        else:
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            redis_client.ping()
            results.append(("Redis", True, f"Connected to {settings.redis_url}"))
    except Exception as e:
        results.append(("Redis", False, f"Connection failed: {e!s}"))

    # Check PostgreSQL
    try:
        if text is None or get_session is None:
            results.append(("PostgreSQL", False, "Required packages not installed (sqlalchemy, app.db.session)"))
        else:
            with get_session() as db:
                db.execute(text("SELECT 1"))
            db_type = (
                "PostgreSQL" if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower() else "SQLite"
            )
            results.append((db_type, True, "Connected to database"))
    except Exception as e:
        results.append(("PostgreSQL", False, f"Connection failed: {e!s}"))

    # Display results
    all_ok = all(status for _, status, _ in results)
    status_text = "All database connections OK" if all_ok else "Some database connections failed"
    status_style = "bold green" if all_ok else "bold red"
    border_style = "green" if all_ok else "red"

    details = "\n".join([f"  {'✓' if status else '✗'} {name}: {message}" for name, status, message in results])

    console.print(
        Panel(
            Text(status_text, style=status_style),
            subtitle=details,
            border_style=border_style,
        )
    )

    if not all_ok:
        console.print("\n[yellow]Configuration help:[/yellow]")
        console.print("  Redis: Set REDIS_URL environment variable (default: redis://localhost:6379/0)")
        console.print("  PostgreSQL: Set DATABASE_URL environment variable")
        console.print("    Example: postgresql://user:password@localhost:5432/dbname")  # pragma: allowlist secret
        raise typer.Exit(1)


def _get_athlete_id_from_user_id(user_id: str) -> tuple[int | None, bool]:
    """Get athlete_id from user_id via AthleteProfile or StravaAccount.

    Checks both AthleteProfile (which has direct user_id -> athlete_id mapping)
    and StravaAccount (which has user_id -> athlete_id via Strava).

    Args:
        user_id: User ID (Clerk UUID)

    Returns:
        Tuple of (athlete_id or None, user_exists: bool)
    """
    try:
        with get_session() as session:
            # First check if user exists
            user = session.execute(select(User).where(User.id == user_id)).first()
            user_exists = user is not None

            # Try AthleteProfile first (direct mapping)
            profile = session.execute(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
            if profile and profile[0].athlete_id:
                return (int(profile[0].athlete_id), user_exists)

            # Fallback to StravaAccount
            account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
            if account:
                return (int(account[0].athlete_id), user_exists)
            return (None, user_exists)
    except Exception as e:
        logger.warning(f"Failed to look up athlete_id from user_id: {e}")
        return (None, False)


@app.command()
def client(
    input_text: str | None = typer.Option(None, "--input", "-i", help="One-shot input text"),
    athlete_id: int | None = typer.Option(None, "--athlete-id", help="Athlete ID (optional if user-id provided)"),
    user_id: str = typer.Option(DEFAULT_USER_ID, "--user-id", help="User ID (Clerk UUID) - will look up athlete_id if not provided"),
    days: int = typer.Option(DEFAULT_DAYS, "--days", help="Number of days of training data"),
    days_to_race: int | None = typer.Option(None, "--days-to-race", help="Days until race"),
    output_file: str | None = typer.Option(None, "--output", "-o", help="Write output to file"),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty", help="Pretty print JSON output"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Run the orchestrator locally.

    Supports both interactive and one-shot execution modes.
    All operations go through MCP clients (no direct DB/FS access).

    If user_id is provided but athlete_id is not, athlete_id will be looked up from the database.
    """
    # If athlete_id not provided but user_id is, try to look it up
    resolved_athlete_id = athlete_id
    if resolved_athlete_id is None and user_id != DEFAULT_USER_ID:
        # Log database connection info for debugging
        db_url_preview = str(settings.database_url)
        if len(db_url_preview) > 50:
            db_url_preview = db_url_preview[:47] + "..."
        logger.info(f"Using database: {db_url_preview}")
        logger.info(f"Looking up athlete_id for user_id: {user_id}")
        found_athlete_id, user_exists = _get_athlete_id_from_user_id(user_id)
        if found_athlete_id is None:
            console.print(f"[red]Error:[/red] Could not find athlete_id for user_id: {user_id}")
            if user_exists:
                console.print("[yellow]User exists in database but has not connected their Strava account.[/yellow]")
                console.print("[yellow]Please connect Strava account first, or provide --athlete-id directly.[/yellow]")
            else:
                console.print("[yellow]User not found in database.[/yellow]")
                # Check if we're using SQLite when data might be in PostgreSQL
                db_url = str(settings.database_url) if hasattr(settings, "database_url") else "unknown"
                if "sqlite" in db_url.lower():
                    console.print(
                        "[yellow]⚠️  Currently using SQLite database. "
                        "If your data is in PostgreSQL, set DATABASE_URL environment variable.[/yellow]"
                    )
                    console.print(
                        "[yellow]Example: DATABASE_URL='postgresql://user:pass@host:5432/db'[/yellow]"  # pragma: allowlist secret
                    )
                console.print("[yellow]Please provide a valid user_id or use --athlete-id directly.[/yellow]")
            raise typer.Exit(1)
        resolved_athlete_id = found_athlete_id
        logger.info(f"Found athlete_id: {resolved_athlete_id} for user_id: {user_id}")
    elif resolved_athlete_id is None:
        # Use default if neither provided
        resolved_athlete_id = DEFAULT_ATHLETE_ID

    config = ClientConfig(
        input_text=input_text,
        athlete_id=resolved_athlete_id,
        user_id=user_id,
        days=days,
        days_to_race=days_to_race,
        output_file=output_file,
        pretty=pretty,
    )
    _run_client_with_config(config, debug)


def _run_client_with_config(config: ClientConfig, debug: bool) -> None:
    """Internal implementation of client command with reduced parameter count."""
    # Set up logging
    _setup_logging(debug=debug)

    # Validate MCP servers
    try:
        _validate_mcp_servers()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}", style="bold red")
        raise typer.Exit(1) from e

    # Run async client
    asyncio.run(_run_client_async(config))


async def _run_client_async(config: ClientConfig) -> None:
    """Async implementation of client command.

    Args:
        config: Client configuration
    """
    # Create dependencies
    # Note: athlete_state is None - orchestrator tools will use MCP to get data when needed
    deps = CoachDeps(
        athlete_id=config.athlete_id,
        user_id=config.user_id,
        athlete_state=None,  # Will be populated by tools via MCP if needed
        athlete_profile=None,
        days=config.days,
        days_to_race=config.days_to_race,
    )

    # Interactive mode
    if config.input_text is None:
        console.print(
            Panel(
                Text("Virtus AI Orchestrator CLI - Interactive Mode", style="bold cyan"),
                subtitle="Enter your message (press Enter with empty text, or type EXIT/QUIT to exit)",
                border_style="cyan",
            )
        )
        console.print(f"[dim]Athlete ID: {config.athlete_id}, User ID: {config.user_id}, Days: {config.days}[/dim]\n")

        while True:
            try:
                user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()

                # Exit conditions: empty input, EXIT, or QUIT
                if not user_input:
                    console.print("[yellow]Empty input detected. Exiting...[/yellow]")
                    break
                if user_input.upper() in {"EXIT", "QUIT"}:
                    console.print("[yellow]Exiting...[/yellow]")
                    break

                # Run orchestrator
                await _run_orchestrator_single(
                    user_input=user_input,
                    deps=deps,
                    output_file=None,  # Don't write to file in interactive mode
                    _pretty=config.pretty,
                )

                console.print()  # Blank line for readability

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted. Exiting...[/yellow]")
                break
            except Exception as e:
                logger.exception(f"Error in interactive mode: {e}")
                console.print(f"[red]Error:[/red] {e}", style="bold red")

    # One-shot mode
    else:
        if config.input_text is None:
            raise ValueError("input_text cannot be None in one-shot mode")
        await _run_orchestrator_single(
            user_input=config.input_text,
            deps=deps,
            output_file=config.output_file,
            _pretty=config.pretty,
        )


async def _run_orchestrator_single(
    user_input: str,
    deps: CoachDeps,
    output_file: str | None,
    _pretty: bool,  # Kept for API compatibility, not currently used
) -> None:
    """Run orchestrator for a single input.

    Args:
        user_input: User input text
        deps: Coach dependencies
        output_file: Optional output file path
        pretty: Whether to pretty-print JSON (kept for compatibility, not currently used)
    """
    logger.info(
        "Running orchestrator",
        user_input_preview=user_input[:100],
        athlete_id=deps.athlete_id,
        user_id=deps.user_id,
    )

    try:
        # Generate a conversation_id for CLI (not a real conversation_id, but needed for tool execution)
        conversation_id = f"cli_{deps.athlete_id}_{int(datetime.now(UTC).timestamp())}"

        # Call the same entry point as the API
        reply = await process_coach_chat(
            message=user_input,
            user_id=deps.user_id or f"cli_user_{deps.athlete_id}",
            athlete_id=deps.athlete_id,
            conversation_id=conversation_id,
            days=deps.days,
            days_to_race=deps.days_to_race,
        )

        # Display the coach's reply
        console.print("\n[bold cyan]Coach:[/bold cyan]")
        console.print(f"{reply}")
        console.print()  # Blank line at end

        logger.info(
            "Coach chat completed",
            athlete_id=deps.athlete_id,
            reply_length=len(reply),
        )

        # Write to file if specified
        if output_file:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Use synchronous file write in async context (acceptable for CLI)
            # Write file synchronously to avoid async file I/O complexity in CLI
            _write_file_sync(output_path, reply)
            logger.info(f"Output written to {output_file}")

    except Exception as e:
        logger.exception(f"Orchestrator error: {e}")
        console.print(f"[red]Error:[/red] {e}", style="bold red")
        raise


if __name__ == "__main__":
    app()
