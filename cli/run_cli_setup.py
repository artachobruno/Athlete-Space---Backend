#!/usr/bin/env python3
"""Virtus MCP CLI Setup Script.

Launches MCP DB Server, MCP FS Server, and CLI in separate Terminal windows.
Works on macOS (using osascript) and provides instructions for other platforms.
"""

import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def get_project_root() -> Path:
    """Get the project root directory."""
    script_dir = Path(__file__).parent.resolve()
    return script_dir.parent


def open_terminal_macos(title: str, command: str, project_root: Path) -> None:
    """Open a new Terminal window on macOS and run a command.

    Args:
        title: Title for the terminal window
        command: Command to run
        project_root: Project root directory
    """
    applescript = f"""
tell application "Terminal"
    activate
    set newTab to do script "cd '{project_root}' && {command}"
    set custom title of newTab to "{title}"
end tell
"""
    subprocess.run(["osascript", "-e", applescript], check=True)


def print_instructions(title: str, command: str, project_root: Path) -> None:
    """Print instructions for running a command manually.

    Args:
        title: Title/description of the command
        command: Command to run
        project_root: Project root directory
    """
    print(f"\n{title}:")
    print(f"  cd {project_root}")
    print(f"  {command}\n")


def main() -> None:
    """Main entry point."""
    project_root = get_project_root()
    is_macos = platform.system() == "Darwin"

    print("Virtus MCP CLI Setup")
    print("====================")
    print()

    if not is_macos:
        print("⚠️  Warning: This script is optimized for macOS.")
        print("On other systems, you'll need to run the servers manually.\n")

    # Terminal 1: MCP DB Server
    print("✓ Starting MCP DB Server (Terminal 1)...")
    db_command = "python mcp/db_server/main.py"
    if is_macos:
        open_terminal_macos("MCP DB Server", db_command, project_root)
        time.sleep(2)
    else:
        print_instructions("MCP DB Server", db_command, project_root)

    # Terminal 2: MCP FS Server
    print("✓ Starting MCP FS Server (Terminal 2)...")
    fs_command = "python mcp/fs_server/main.py"
    if is_macos:
        open_terminal_macos("MCP FS Server", fs_command, project_root)
        time.sleep(2)
    else:
        print_instructions("MCP FS Server", fs_command, project_root)

    # Terminal 3: CLI Client
    print("✓ Starting CLI Client (Terminal 3)...")
    cli_command = (
        "export MCP_DB_SERVER_URL=http://localhost:8080 && "
        "export MCP_FS_SERVER_URL=http://localhost:8081 && "
        "echo 'MCP environment variables set' && "
        "echo 'MCP_DB_SERVER_URL=$MCP_DB_SERVER_URL' && "
        "echo 'MCP_FS_SERVER_URL=$MCP_FS_SERVER_URL' && "
        "echo '' && "
        "echo 'Waiting 3 seconds for servers to start...' && "
        "sleep 3 && "
        "python cli/cli.py check-mcp && "
        "echo '' && "
        "echo 'Starting interactive CLI...' && "
        "python cli/cli.py client"
    )
    if is_macos:
        open_terminal_macos("Virtus CLI", cli_command, project_root)
    else:
        print_instructions("Virtus CLI", cli_command, project_root)

    print()
    print("✓ Setup complete!")
    print()
    print("Three Terminal windows should now be open:")
    print("  1. MCP DB Server (port 8080)")
    print("  2. MCP FS Server (port 8081)")
    print("  3. Virtus CLI (interactive mode)")
    print()
    print("Note: If you need to set OPENAI_API_KEY, add it to Terminal 3:")
    print("  export OPENAI_API_KEY=your_key_here")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\n\nError: Failed to open terminal window: {e}")
        sys.exit(1)
