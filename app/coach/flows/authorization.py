"""Phase B: Authorization Gate (HARD STOP).

This module manages authorization state for plan mutations.
No mutation may proceed without explicit user approval.
"""

from datetime import datetime, timezone
from typing import Literal

from loguru import logger

from app.coach.services.conversation_progress import create_or_update_progress, get_conversation_progress

AuthorizationState = Literal["pending", "approved", "rejected", "none"]


def parse_authorization_response(user_input: str) -> AuthorizationState:
    """Parse user input to determine authorization state.

    Args:
        user_input: User's response to authorization request

    Returns:
        AuthorizationState: "approved", "rejected", or "pending" (if unclear)
    """
    normalized = user_input.lower().strip()

    # Approval keywords
    approval_keywords = ["yes", "approve", "proceed", "go ahead", "ok", "okay", "sure", "y", "yeah"]
    if any(keyword in normalized for keyword in approval_keywords):
        return "approved"

    # Rejection keywords
    rejection_keywords = ["no", "cancel", "abort", "stop", "n", "nope", "don't", "dont"]
    if any(keyword in normalized for keyword in rejection_keywords):
        return "rejected"

    # Unclear - need clarification
    return "pending"


def get_authorization_state(conversation_id: str) -> AuthorizationState:
    """Get current authorization state for a conversation.

    Args:
        conversation_id: Conversation ID

    Returns:
        AuthorizationState: Current state ("none" if not set)
    """
    progress = get_conversation_progress(conversation_id)
    if not progress:
        return "none"

    # Authorization state is stored in slots as "authorization_state"
    slots = progress.slots or {}
    auth_state = slots.get("authorization_state")

    if auth_state in ["pending", "approved", "rejected"]:
        return auth_state

    return "none"


def set_authorization_state(
    conversation_id: str,
    state: AuthorizationState,
    user_id: str | None = None,
) -> None:
    """Set authorization state for a conversation.

    Args:
        conversation_id: Conversation ID
        state: Authorization state to set
        user_id: Optional user ID (for progress creation)
    """
    logger.info(
        "Setting authorization state",
        conversation_id=conversation_id,
        state=state,
    )

    # Get current progress
    progress = get_conversation_progress(conversation_id)
    current_slots = progress.slots if progress else {}

    # Update authorization state in slots
    updated_slots = {**current_slots, "authorization_state": state, "authorization_timestamp": datetime.now(timezone.utc).isoformat()}

    # Persist to conversation progress
    create_or_update_progress(
        conversation_id=conversation_id,
        slots=updated_slots,
        user_id=user_id,
        clear_on_intent_change=False,  # Don't clear on intent change for authorization
    )

    logger.info(
        "Authorization state updated",
        conversation_id=conversation_id,
        state=state,
    )


def require_authorization(
    conversation_id: str,
    tool_name: str,
) -> None:
    """Require authorization before mutation.

    HARD RULE: Mutation tools MUST have authorization_state == "approved".

    Args:
        conversation_id: Conversation ID
        tool_name: Tool name being executed

    Raises:
        RuntimeError: If authorization is not approved
    """
    auth_state = get_authorization_state(conversation_id)

    if auth_state != "approved":
        logger.warning(
            "Mutation attempted without authorization",
            conversation_id=conversation_id,
            tool_name=tool_name,
            auth_state=auth_state,
        )
        raise RuntimeError(
            f"Tool '{tool_name}' requires authorization. "
            f"Current authorization state: {auth_state}. "
            "Please approve the proposal first."
        )

    logger.debug(
        "Authorization check passed",
        conversation_id=conversation_id,
        tool_name=tool_name,
    )


def clear_authorization_state(conversation_id: str) -> None:
    """Clear authorization state (e.g., when starting new proposal).

    Args:
        conversation_id: Conversation ID
    """
    progress = get_conversation_progress(conversation_id)
    if not progress:
        return

    current_slots = progress.slots or {}
    updated_slots = {k: v for k, v in current_slots.items() if k not in ["authorization_state", "authorization_timestamp"]}

    create_or_update_progress(
        conversation_id=conversation_id,
        slots=updated_slots,
        clear_on_intent_change=False,
    )

    logger.info(
        "Authorization state cleared",
        conversation_id=conversation_id,
    )
