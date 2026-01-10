import sys

import typer

import cli.cli

if __name__ == "__main__":
    # Set up command-line arguments for Typer
    sys.argv = ["run_cli.py", "client", "--user-id", "1dbd1d2b-f2fa-4482-b077-d93da528cfc5"]
    # Access app attribute - it's a Typer instance defined in cli.cli module
    typer_app: typer.Typer = cli.cli.app
    typer_app()
