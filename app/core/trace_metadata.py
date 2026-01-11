"""Trace metadata helpers for Observe integration.

Provides utilities to extract and format trace metadata from request context.
"""

from typing import Any


def get_trace_metadata(
    conversation_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    environment: str | None = None,
) -> dict[str, str]:
    """Build trace metadata dictionary from context.

    Args:
        conversation_id: Conversation ID
        user_id: User ID
        request_id: Request ID (optional, can be generated from conversation_id)
        environment: Environment name (dev/staging/prod)

    Returns:
        Dictionary of trace metadata
    """
    metadata: dict[str, str] = {}

    if conversation_id:
        metadata["conversation_id"] = conversation_id

    if user_id:
        metadata["user_id"] = user_id

    if request_id:
        metadata["request_id"] = request_id
    elif conversation_id:
        # Use conversation_id as request_id if not provided
        metadata["request_id"] = conversation_id

    if environment:
        metadata["env"] = environment
    else:
        # Auto-detect environment
        import os

        if os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT"):
            metadata["env"] = "prod"
        elif os.getenv("STAGING"):
            metadata["env"] = "staging"
        else:
            metadata["env"] = "dev"

    return metadata


def get_trace_metadata_from_deps(
    deps: Any,
    conversation_id: str | None = None,
) -> dict[str, str]:
    """Extract trace metadata from CoachDeps object.

    Args:
        deps: CoachDeps object with user_id and other context
        conversation_id: Optional conversation ID

    Returns:
        Dictionary of trace metadata
    """
    user_id = getattr(deps, "user_id", None)
    return get_trace_metadata(
        conversation_id=conversation_id,
        user_id=user_id if user_id else None,
    )
