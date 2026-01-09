"""Conversation ID middleware and utilities.

Handles conversation ID extraction, validation, and generation for all requests.
"""

import re
import uuid

from fastapi import HTTPException, Request, status
from loguru import logger

# Conversation ID format: c_<UUID>
# Supports UUID v4 and v7
CONVERSATION_ID_PATTERN = re.compile(r"^c_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
CONVERSATION_ID_HEADER = "X-Conversation-Id"


def generate_conversation_id() -> str:
    """Generate a new conversation ID in the required format.

    Returns:
        Conversation ID string in format: c_<UUID>
    """
    return f"c_{uuid.uuid4()}"


def validate_conversation_id(conversation_id: str) -> bool:
    """Validate conversation ID format.

    Args:
        conversation_id: Conversation ID to validate

    Returns:
        True if valid, False otherwise
    """
    return bool(CONVERSATION_ID_PATTERN.match(conversation_id))


async def conversation_id_middleware(request: Request, call_next):
    """Middleware to extract, validate, and generate conversation IDs.

    - Reads X-Conversation-Id from request headers
    - If missing: generates fallback ID and logs warning
    - If present: validates format and hard-fails on invalid format
    - Attaches conversation_id to request.state
    - Skips validation for OPTIONS requests (CORS preflight)

    Args:
        request: FastAPI request object
        call_next: Next middleware/handler in chain

    Returns:
        Response from next handler

    Raises:
        HTTPException: If conversation_id format is invalid
    """
    # Skip validation for OPTIONS requests (CORS preflight)
    # Still generate/attach conversation_id for consistency
    if request.method == "OPTIONS":
        header_value = request.headers.get(CONVERSATION_ID_HEADER)
        if header_value and validate_conversation_id(header_value):
            conversation_id = header_value
        else:
            conversation_id = generate_conversation_id()
        request.state.conversation_id = conversation_id
        return await call_next(request)

    # Extract header
    header_value = request.headers.get(CONVERSATION_ID_HEADER)

    if not header_value:
        # Generate fallback ID
        conversation_id = generate_conversation_id()
        logger.warning(
            "Missing X-Conversation-Id header, generated fallback",
            conversation_id=conversation_id,
            path=request.url.path,
            method=request.method,
        )
    else:
        # Validate format
        if not validate_conversation_id(header_value):
            logger.error(
                "Invalid conversation_id format",
                received_value=header_value,
                path=request.url.path,
                method=request.method,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid conversation_id format. Expected format: c_<UUID>. Received: {header_value}",
            )
        conversation_id = header_value

    # Attach to request state
    request.state.conversation_id = conversation_id

    # Continue request processing
    return await call_next(request)


def get_conversation_id(request: Request) -> str:
    """Retrieve conversation_id from request context.

    This is the strict accessor utility. It raises immediately if missing.

    Args:
        request: FastAPI request object

    Returns:
        Conversation ID string

    Raises:
        RuntimeError: If conversation_id is not present in request.state
    """
    if not hasattr(request.state, "conversation_id"):
        raise RuntimeError(
            "conversation_id not found in request.state. Ensure conversation_id_middleware is registered before this handler."
        )

    conversation_id = request.state.conversation_id
    if not conversation_id:
        raise RuntimeError("conversation_id is None in request.state")

    return conversation_id
