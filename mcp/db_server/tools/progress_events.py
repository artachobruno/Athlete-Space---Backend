"""Progress event tools for MCP DB server."""

import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.db.models import CoachProgressEvent
from app.db.session import get_session
from mcp.db_server.errors import MCPError


def emit_progress_event_tool(arguments: dict) -> dict:
    """Emit a progress event for coach orchestrator observability.

    Contract:
    - conversation_id: string (required)
    - step_id: string (required)
    - label: string (required)
    - status: string (required) - one of: "planned", "in_progress", "completed", "failed", "skipped"
    - message: string (optional)

    Returns:
        dict with "success": true
    """
    conversation_id = arguments.get("conversation_id")
    step_id = arguments.get("step_id")
    label = arguments.get("label")
    status = arguments.get("status")
    message = arguments.get("message")

    # Validate inputs
    if not conversation_id or not isinstance(conversation_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid required field: conversation_id (must be string)")
    if not step_id or not isinstance(step_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid required field: step_id (must be string)")
    if not label or not isinstance(label, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid required field: label (must be string)")
    if not status or not isinstance(status, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid required field: status (must be string)")

    valid_statuses = {"planned", "in_progress", "completed", "failed", "skipped"}
    if status not in valid_statuses:
        raise MCPError(
            "INVALID_INPUT",
            f"Invalid status: {status}. Must be one of: {', '.join(valid_statuses)}",
        )

    if message is not None and not isinstance(message, str):
        raise MCPError("INVALID_INPUT", "message must be a string or null")

    # Persist event to database
    try:
        with get_session() as db:
            event = CoachProgressEvent(
                conversation_id=conversation_id,
                step_id=step_id,
                label=label,
                status=status,
                timestamp=datetime.now(UTC),
                message=message,
            )
            db.add(event)
            db.commit()

            logger.info(
                "Progress event emitted",
                conversation_id=conversation_id,
                step_id=step_id,
                label=label,
                status=status,
            )

            return {"success": True}
    except SQLAlchemyError as e:
        logger.error(f"Database error emitting progress event: {e}", exc_info=True)
        raise MCPError("DB_ERROR", f"Failed to persist progress event: {e!s}") from e
    except Exception as e:
        logger.error(f"Unexpected error emitting progress event: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to emit progress event: {e!s}") from e
