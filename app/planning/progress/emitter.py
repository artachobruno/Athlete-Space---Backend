"""Planning Progress Emitter - Phase 6B.

Centralized progress emission for planning phases.
All planning progress emissions go through this function.
"""

from loguru import logger

from app.planning.progress.contracts import SummaryValue


def emit_planning_progress(
    *,
    phase: str,
    status: str,
    percent: int,
    message: str,
    summary: dict[str, SummaryValue] | None = None,
    conversation_id: str | None = None,
) -> None:
    """Emit planning progress event.

    Phase 6B: All planning progress emissions go through this function.
    Events are structured, side-effect free, and emitted at phase boundaries.

    Args:
        phase: Planning phase identifier
        status: Event status ("started" or "completed")
        percent: Progress percentage (0-100, must be monotonically increasing)
        message: Human-readable message for this phase
        summary: Optional structured data (dict only)
        conversation_id: Optional conversation ID for event correlation

    Note:
        This function is designed to be side-effect free and can be called
        even when no event system is available (will log only).
    """
    # Phase 6B: Events are side-effect free - logging is the primary mechanism
    # All planning progress emissions go through this function
    # Log the event (always side-effect free)
    logger.info(
        "[PLANNING_PROGRESS] Planning progress event",
        phase=phase,
        status=status,
        percent_complete=percent,
        message=message,
        summary=summary,
        conversation_id=conversation_id,
    )
