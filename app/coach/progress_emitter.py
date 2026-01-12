"""Progress emitter for plan generation.

This module provides utilities for emitting progress events during plan generation.
Progress events are stored as transient messages that can be updated/replaced.
"""

from loguru import logger

from app.coach.conversation_store import ConversationStore
from app.coach.progress import PlanProgressStage


async def emit_plan_progress(
    conversation_id: str,
    user_id: str,
    stage: PlanProgressStage,
    message: str,
    *,
    metadata: dict[str, str] | None = None,
) -> None:
    """Emit a plan generation progress event.

    Progress events are stored as transient messages that can be updated/replaced
    by the frontend. They are stored in Redis but not persisted to the database.

    Args:
        conversation_id: Conversation ID
        user_id: User ID
        stage: Progress stage
        message: Progress message text
        metadata: Optional additional metadata

    Example:
        await emit_plan_progress(
            conversation_id="c_123",
            user_id="user_456",
            stage=PlanProgressStage.STRUCTURE,
            message="Planning overall structure"
        )
    """
    if not conversation_id:
        logger.debug("Skipping progress emission: no conversation_id")
        return

    if not user_id:
        logger.warning("Skipping progress emission: no user_id", conversation_id=conversation_id)
        return

    # Merge metadata
    progress_metadata: dict[str, str] = metadata.copy() if metadata else {}

    await ConversationStore.append_message(
        conversation_id=conversation_id,
        user_id=user_id,
        role="assistant",
        content=message,
        message_type="progress",
        progress_stage=stage.value,
        metadata=progress_metadata,
        transient=True,
    )

    logger.debug(
        "Plan progress emitted",
        conversation_id=conversation_id,
        stage=stage.value,
        message=message[:50] if len(message) > 50 else message,
    )
