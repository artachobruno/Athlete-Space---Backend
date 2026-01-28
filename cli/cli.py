
"""CLI for MCP-Wired Orchestrator.

Developer CLI to run the system offline/locally that exercises the same
orchestrator code path as production using MCP clients for all DB and filesystem operations.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from sqlalchemy.orm import Session

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.services.chat_service import process_coach_chat
from app.config.settings import settings
from app.core.conversation_id import generate_conversation_id
from app.db.models import Athlete, AthleteProfile, PlannedSession, StravaAccount, User, UserIntegration
from app.db.session import get_session
from app.domains.training_plan.template_loader import initialize_template_library_from_cache
from app.integrations.garmin.backfill import backfill_garmin_activities
from scripts.migrate_activities_garmin_fields import migrate_activities_garmin_fields
from scripts.migrate_garmin_webhook_events import migrate_garmin_webhook_events
from scripts.migrate_user_integrations import migrate_user_integrations

# Initialize Rich console for output
console = Console()

# Initialize Typer app
app = typer.Typer(
    name="AthleteSpace-cli",
    help="AthleteSpace AI CLI - Offline/Local Orchestrator Testing",
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
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{file.name}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level=log_level,
        colorize=True,
    )

    # File handler with rotation
    # Note: All log messages (including full prompts and responses) are captured in the message field
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"cli_{timestamp}.log"

    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {file.name}:{line} - {message}",
        level=log_level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",  # Ensure UTF-8 encoding for proper character support
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
            "Missing MCP server URLs.\n"
            "Deployed servers are used by default (no configuration needed).\n"
            "If you need to override, set:\n"
            "  MCP_DB_SERVER_URL=https://athlete-space-mcp-db.onrender.com\n"
            "  MCP_FS_SERVER_URL=https://athlete-space-mcp-fs.onrender.com"
        )

    # Check if localhost is configured (common mistake)
    localhost_urls = ["http://localhost", "http://127.0.0.1"]
    is_localhost = any(url.startswith(prefix) for url in [db_url, fs_url] for prefix in localhost_urls)

    # Actively probe servers to verify they're reachable
    for name, url in [("DB", db_url), ("FS", fs_url)]:
        try:
            response = httpx.get(f"{url}/health", timeout=2.0)
            response.raise_for_status()
        except httpx.RequestError as e:
            error_msg = f"MCP {name} server not reachable at {url}."
            if is_localhost:
                error_msg += (
                    f"\n\nYou're trying to connect to localhost. "
                    f"To use deployed servers (default), unset the environment variable:\n"
                    f"  unset MCP_{name}_SERVER_URL\n"
                    f"Or explicitly set it to the deployed server:\n"
                    f"  export MCP_{name}_SERVER_URL=https://athlete-space-mcp-{name.lower()}.onrender.com"
                )
            else:
                error_msg += f" Make sure the server is running: {e}"
            raise RuntimeError(error_msg) from e
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
        console.print("\n[yellow]MCP Server Configuration:[/yellow]")
        console.print("\n[green]Default (Deployed Servers - No configuration needed):[/green]")
        console.print("  The CLI uses deployed servers by default:")
        console.print("  - DB: https://athlete-space-mcp-db.onrender.com")
        console.print("  - FS: https://athlete-space-mcp-fs.onrender.com")
        console.print("\n  [dim]If you have MCP_*_SERVER_URL set to localhost, unset them to use defaults:[/dim]")
        console.print("  [dim]  unset MCP_DB_SERVER_URL MCP_FS_SERVER_URL[/dim]")
        console.print("\n[yellow]To use local servers instead:[/yellow]")
        console.print("  Terminal 1: python mcp/db_server/main.py")
        console.print("  Terminal 2: python mcp/fs_server/main.py")
        console.print("  export MCP_DB_SERVER_URL=http://localhost:8080")
        console.print("  export MCP_FS_SERVER_URL=http://localhost:8081")
        raise typer.Exit(1) from e

    db_url = settings.mcp_db_server_url
    fs_url = settings.mcp_fs_server_url
    console.print(
        Panel(
            Text("MCP servers are running", style="bold green"),
            subtitle=f"DB: {db_url}\nFS: {fs_url}",
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


def _resolve_user_id(session: Session, user_id: str) -> str:
    """Resolve user_id from various identifiers.

    Tries multiple strategies:
    1. Email lookup
    2. If "cli-user", lookup by athlete_id=1

    Args:
        session: Database session
        user_id: User identifier (email, "cli-user", etc.)

    Returns:
        Resolved user_id (UUID string)

    Raises:
        typer.Exit: If user_id cannot be resolved
    """
    # Strategy 1: Try to look it up by email
    console.print(f"[yellow]Looking up user_id for: {user_id}[/yellow]")
    user = session.execute(select(User).where(User.email == user_id)).scalar_one_or_none()
    if user:
        resolved_user_id = user.id
        console.print(f"[green]Found user_id: {resolved_user_id}[/green]")
        return resolved_user_id

    # Strategy 2: If "cli-user", try to find user_id from athlete_id=1
    if user_id in {"cli-user", DEFAULT_USER_ID}:
        console.print("[yellow]Looking up user_id for athlete_id=1 (cli-user)...[/yellow]")
        # Try StravaAccount first (athlete_id is stored as string)
        strava_account = session.execute(
            select(StravaAccount).where(StravaAccount.athlete_id == "1")
        ).scalar_one_or_none()
        if strava_account:
            resolved_user_id = str(strava_account.user_id)
            console.print(f"[green]Found user_id from StravaAccount: {resolved_user_id}[/green]")
            return resolved_user_id

        # If no StravaAccount, try to find any user with planned sessions
        # This is a fallback for cli-user
        console.print("[yellow]Trying to find user_id from planned sessions...[/yellow]")
        planned_session = session.execute(select(PlannedSession)).scalar_one_or_none()
        if planned_session:
            resolved_user_id = str(planned_session.user_id)
            console.print(f"[green]Found user_id from PlannedSession: {resolved_user_id}[/green]")
            return resolved_user_id

    # If we get here, couldn't resolve
    console.print(
        f"[red]Error:[/red] Could not find user_id for '{user_id}'. "
        f"Please provide a valid UUID or email address.",
        style="bold red"
    )
    raise typer.Exit(1) from None


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
def delete_planned_sessions(
    user_id: str = typer.Option(DEFAULT_USER_ID, "--user-id", help="User ID (UUID) or email"),
    confirm: bool = typer.Option(False, "--confirm", help="Confirm deletion (required for safety)"),
) -> None:
    """Delete all planned sessions for a user.

    This will permanently delete all planned training sessions for the specified user.
    Use with caution as this action cannot be undone.

    The user_id can be either:
    - A UUID (e.g., "805f6d98-c1eb-4fef-8531-410fd4879979")
    - An email address (will look up the user_id)
    """
    if not confirm:
        console.print("[red]Error:[/red] --confirm flag is required for safety", style="bold red")
        console.print("Usage: delete-planned-sessions --user-id <user_id> --confirm")
        raise typer.Exit(1)

    try:
        with get_session() as session:
            # Resolve user_id: if not a UUID, try to look it up by email or athlete_id
            resolved_user_id = user_id
            try:
                # Try to validate as UUID
                uuid.UUID(user_id)
            except ValueError:
                # Not a UUID, try different lookup strategies
                resolved_user_id = _resolve_user_id(session, user_id)

            # Count planned sessions before deletion
            count_query = select(PlannedSession).where(PlannedSession.user_id == resolved_user_id)
            planned_sessions = session.scalars(count_query).all()
            count = len(planned_sessions)

            if count == 0:
                console.print(f"[yellow]No planned sessions found for user_id: {resolved_user_id}[/yellow]")
                return

            console.print(f"[yellow]Found {count} planned sessions for user_id: {resolved_user_id}[/yellow]")
            console.print("[red]Deleting all planned sessions...[/red]")

            # Delete all planned sessions
            delete_query = select(PlannedSession).where(PlannedSession.user_id == resolved_user_id)
            deleted_count = 0
            for planned_session in session.scalars(delete_query).all():
                session.delete(planned_session)
                deleted_count += 1

            session.commit()
            console.print(f"[green]Successfully deleted {deleted_count} planned sessions[/green]")
            logger.info(f"Deleted {deleted_count} planned sessions for user_id={resolved_user_id}")

    except Exception as e:
        logger.exception(f"Error deleting planned sessions: {e}")
        console.print(f"[red]Error:[/red] {e}", style="bold red")
        raise typer.Exit(1) from e


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

    # Initialize template library (required for planner)
    try:
        logger.info("Initializing template library from cache")
        initialize_template_library_from_cache()
        logger.info("Template library initialized successfully")
    except Exception as e:
        logger.exception("Failed to initialize template library: {}", e)
        console.print(
            "[yellow]Warning:[/yellow] Template library not initialized. "
            "Planner will not work until templates are precomputed.\n"
            "Run: python scripts/precompute_embeddings.py templates"
        )
        # Don't exit - allow CLI to run but planner will fail with clear error

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
                Text("AthleteSpace AI Orchestrator CLI - Interactive Mode", style="bold cyan"),
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
        # Generate a conversation_id for CLI (uses proper format: c_<UUID>)
        conversation_id = generate_conversation_id()

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


@app.command()
def backfill_garmin(
    user_id: str = typer.Argument(..., help="User ID (UUID) to backfill for"),
    force: bool = typer.Option(False, "--force", "-f", help="Force backfill even if recently synced"),
    days: int = typer.Option(None, "--days", "-d", help="Number of days to backfill (default: GARMIN_BACKFILL_DAYS or 90)"),
) -> None:
    """Trigger Garmin activities backfill for a user.

    Examples:
        # Backfill last 90 days (default)
        python cli/cli.py backfill-garmin <user_id>

        # Force backfill even if recently synced
        python cli/cli.py backfill-garmin <user_id> --force

        # Backfill last 30 days
        python cli/cli.py backfill-garmin <user_id> --days 30
    """
    def exit_with_error(message: str, code: int = 1) -> None:
        """Exit with error message."""
        console.print(f"[red]Error:[/red] {message}")
        raise typer.Exit(code)

    console.print(f"[bold cyan]Starting Garmin backfill for user_id={user_id}...[/bold cyan]\n")

    # Verify user has Garmin integration
    try:
        with get_session() as session:
            integration = session.execute(
                select(UserIntegration).where(
                    UserIntegration.user_id == user_id,
                    UserIntegration.provider == "garmin",
                    UserIntegration.revoked_at.is_(None),
                )
            ).first()

            if not integration:
                exit_with_error(f"No active Garmin integration found for user_id={user_id}")

            console.print(f"[green]✓ Found Garmin integration for user_id={user_id}[/green]\n")
    except Exception as e:
        console.print(f"[red]Error checking integration:[/red] {e}")
        logger.exception("Failed to check Garmin integration")
        raise typer.Exit(1) from e

    # Calculate date range if days specified
    from_date = None
    to_date = None
    if days:
        to_date = datetime.now(UTC)
        from_date = to_date - timedelta(days=days)
        console.print(f"[cyan]Backfill window:[/cyan] {from_date.date()} to {to_date.date()} ({days} days)\n")

    # Run backfill
    try:
        console.print("[yellow]Running backfill...[/yellow]")
        result = backfill_garmin_activities(
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
            force=force,
        )

        console.print("\n[bold green]Backfill Results:[/bold green]")
        console.print(f"  Status: {result.get('status', 'unknown')}")
        console.print(f"  Imported: {result.get('ingested_count', 0)}")
        console.print(f"  Skipped: {result.get('skipped_count', 0)}")
        console.print(f"    - Duplicates: {result.get('duplicate_count', 0)}")
        console.print(f"    - Strava duplicates: {result.get('strava_duplicate_count', 0)}")
        console.print(f"  Errors: {result.get('error_count', 0)}")
        console.print(f"  Total fetched: {result.get('total_fetched', 0)}")

        if int(result.get("error_count", 0)) > 0:
            console.print(f"\n[yellow]⚠️  Backfill completed with {result.get('error_count')} errors[/yellow]")
            console.print("[yellow]Check logs for details[/yellow]")
            exit_with_error("Backfill completed with errors", code=1)

        console.print("\n[bold green]✓ Backfill completed successfully![/bold green]")

    except Exception as e:
        console.print(f"[bold red]✗ Backfill failed:[/bold red] {e}")
        logger.exception("Garmin backfill failed")
        raise typer.Exit(1) from e


@app.command()
def migrate_garmin(
    all: bool = typer.Option(False, "--all", "-a", help="Run all Garmin migrations"),
    user_integrations: bool = typer.Option(False, "--user-integrations", help="Run user_integrations migration"),
    webhook_events: bool = typer.Option(False, "--webhook-events", help="Run garmin_webhook_events migration"),
    activities: bool = typer.Option(False, "--activities", help="Run activities Garmin fields migration"),
) -> None:
    """Run Garmin integration database migrations.

    Examples:
        # Run all Garmin migrations
        python cli/cli.py migrate-garmin --all

        # Run specific migration
        python cli/cli.py migrate-garmin --user-integrations
    """
    console.print("[bold cyan]Running Garmin migrations...[/bold cyan]\n")

    if all:
        user_integrations = True
        webhook_events = True
        activities = True

    if not (user_integrations or webhook_events or activities):
        console.print("[yellow]No migrations selected. Use --all or specify individual migrations.[/yellow]")
        return

    try:
        if user_integrations:
            console.print("[cyan]Running migration: user_integrations table...[/cyan]")
            migrate_user_integrations()
            console.print("[green]✓ user_integrations migration completed[/green]\n")

        if webhook_events:
            console.print("[cyan]Running migration: garmin_webhook_events table...[/cyan]")
            migrate_garmin_webhook_events()
            console.print("[green]✓ garmin_webhook_events migration completed[/green]\n")

        if activities:
            console.print("[cyan]Running migration: activities Garmin fields...[/cyan]")
            migrate_activities_garmin_fields()
            console.print("[green]✓ activities Garmin fields migration completed[/green]\n")

        console.print("[bold green]✓ All selected Garmin migrations completed successfully![/bold green]")

    except Exception as e:
        console.print(f"[bold red]✗ Migration failed:[/bold red] {e}")
        logger.exception("Garmin migration failed")
        raise typer.Exit(code=1) from e


if __name__ == "__main__":
    app()
