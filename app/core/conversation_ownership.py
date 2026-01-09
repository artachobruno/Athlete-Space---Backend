"""Conversation ownership validation and enforcement.

Ensures every conversation_id is owned by exactly one authenticated user.
This is a hard security boundary before Redis, memory, or summaries.

Core invariant: A (conversation_id, user_id) pair is immutable once established.
"""

from fastapi import Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.core.conversation_id import get_conversation_id
from app.db.models import ConversationOwnership
from app.db.session import get_session


def validate_conversation_ownership(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> str:
    """Validate and enforce conversation ownership.

    This guard runs on all conversational reads/writes:
    1. Reads conversation_id from request context
    2. Reads user_id from auth context (required)
    3. Checks ownership store

    Logic:
    - If conversation_id does not exist: Create ownership record (conversation_id → user_id)
    - If conversation_id exists: Validate user_id matches owner
    - If mismatch → 403 Forbidden

    Args:
        request: FastAPI request object
        user_id: Authenticated user ID (from auth dependency, required)

    Returns:
        Authenticated user_id

    Raises:
        HTTPException: 401 if unauthenticated (from get_current_user_id)
        HTTPException: 403 if ownership mismatch
        RuntimeError: If conversation_id is missing from request state
    """
    # Get conversation_id from request context
    conversation_id = get_conversation_id(request)

    # Check ownership store
    with get_session() as db:
        ownership = db.execute(select(ConversationOwnership).where(ConversationOwnership.conversation_id == conversation_id)).first()

        if ownership is None:
            # Conversation doesn't exist - create ownership record
            new_ownership = ConversationOwnership(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            db.add(new_ownership)
            db.commit()

            logger.info(
                "Conversation ownership created",
                conversation_id=conversation_id,
                user_id=user_id,
                event="ownership_created",
            )
        else:
            # Conversation exists - validate ownership
            owner_user_id = ownership.user_id
            if owner_user_id != user_id:
                logger.error(
                    "Conversation ownership violation",
                    conversation_id=conversation_id,
                    requesting_user_id=user_id,
                    owner_user_id=owner_user_id,
                    event="ownership_violation",
                    path=request.url.path,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: This conversation belongs to another user",
                )

            logger.debug(
                "Conversation ownership validated",
                conversation_id=conversation_id,
                user_id=user_id,
                event="ownership_validated",
            )

    return user_id


def get_conversation_owner(conversation_id: str) -> str | None:
    """Get the owner user_id for a conversation.

    Args:
        conversation_id: Conversation ID to look up

    Returns:
        Owner user_id if conversation exists, None otherwise
    """
    with get_session() as db:
        ownership = db.execute(select(ConversationOwnership).where(ConversationOwnership.conversation_id == conversation_id)).first()
        if ownership is None:
            return None
        return ownership.user_id
