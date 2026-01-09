"""CLI for MCP-Wired Orchestrator.

Developer CLI to run the system offline/locally that exercises the same
orchestrator code path as production using MCP clients for all DB and filesystem operations.
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path

import httpx
import typer
import uvicorn
from loguru import logger
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.text import Text

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

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import CoachDeps
from app.config.settings import settings

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
def client(
    input_text: str | None = typer.Option(None, "--input", "-i", help="One-shot input text"),
    athlete_id: int = typer.Option(DEFAULT_ATHLETE_ID, "--athlete-id", help="Athlete ID"),
    user_id: str = typer.Option(DEFAULT_USER_ID, "--user-id", help="User ID (Clerk)"),
    days: int = typer.Option(DEFAULT_DAYS, "--days", help="Number of days of training data"),
    days_to_race: int | None = typer.Option(None, "--days-to-race", help="Days until race"),
    output_file: str | None = typer.Option(None, "--output", "-o", help="Write output to file"),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty", help="Pretty print JSON output"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Run the orchestrator locally.

    Supports both interactive and one-shot execution modes.
    All operations go through MCP clients (no direct DB/FS access).
    """
    config = ClientConfig(
        input_text=input_text,
        athlete_id=athlete_id,
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
                subtitle="Enter your message (empty line, EXIT, or QUIT to exit)",
                border_style="cyan",
            )
        )
        console.print(f"[dim]Athlete ID: {config.athlete_id}, User ID: {config.user_id}, Days: {config.days}[/dim]\n")

        while True:
            try:
                user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()

                # Exit conditions
                if not user_input or user_input.upper() in {"EXIT", "QUIT"}:
                    console.print("[yellow]Exiting...[/yellow]")
                    break

                # Run orchestrator
                await _run_orchestrator_single(
                    user_input=user_input,
                    deps=deps,
                    output_file=None,  # Don't write to file in interactive mode
                    pretty=config.pretty,
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
            pretty=config.pretty,
        )


async def _run_orchestrator_single(
    user_input: str,
    deps: CoachDeps,
    output_file: str | None,
    pretty: bool,
) -> None:
    """Run orchestrator for a single input.

    Args:
        user_input: User input text
        deps: Coach dependencies
        output_file: Optional output file path
        pretty: Whether to pretty-print JSON
    """
    logger.info(
        "Running orchestrator",
        user_input_preview=user_input[:100],
        athlete_id=deps.athlete_id,
        user_id=deps.user_id,
    )

    try:
        # Call orchestrator (this is the ONLY entry point - no direct DB/FS access)
        result = await run_conversation(user_input=user_input, deps=deps)

        # Convert to dict for output
        response_dict = result.model_dump()

        # Format output
        output_text = _format_response(response_dict, pretty=pretty)

        # Display output
        if pretty:
            console.print(Panel(JSON(output_text), title="Orchestrator Response", border_style="green"))
        else:
            console.print(output_text)

        # Write to file if specified
        if output_file:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Use synchronous file write in async context (acceptable for CLI)
            # Write file synchronously to avoid async file I/O complexity in CLI
            _write_file_sync(output_path, output_text)
            logger.info(f"Output written to {output_file}")

        # Log success
        logger.info(
            "Orchestrator completed",
            intent=result.intent,
            response_type=result.response_type,
            message_length=len(result.message),
        )

    except Exception as e:
        logger.exception(f"Orchestrator error: {e}")
        console.print(f"[red]Error:[/red] {e}", style="bold red")
        raise


if __name__ == "__main__":
    app()
