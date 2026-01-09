"""Canonical message schema and normalization.

This module defines the single source of truth for message representation
throughout the system. All messages entering the system must be normalized
into this schema before storage, token counting, prompt assembly, or analytics.

Core invariant: All downstream systems operate only on normalized messages.
Raw request payloads are never trusted beyond ingestion.

B25: Every normalized message has a token count attached before storage or use.
"""

import json
from datetime import datetime, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from app.core.token_counting import count_tokens


class Message(BaseModel):
    """Canonical message schema.

    This is the ONLY message representation allowed past ingestion.
    All messages (user, assistant, system) must be normalized into this shape.

    Fields:
        conversation_id: Conversation ID in format c_<UUID>
        user_id: User ID (Clerk user ID)
        role: Message role - must be one of: user, assistant, system
        content: Message content as string (always trimmed)
        ts: Server-generated ISO-8601 timestamp (UTC)
        tokens: Token count (mandatory, populated during normalization)
        metadata: Optional metadata dictionary (defaults to empty dict)
    """

    conversation_id: str = Field(..., description="Conversation ID in format c_<UUID>")
    user_id: str = Field(..., description="User ID (Clerk user ID)")
    role: Literal["user", "assistant", "system"] = Field(..., description="Message role")
    content: str = Field(..., description="Message content as string")
    ts: str = Field(..., description="ISO-8601 timestamp (UTC)")
    tokens: int = Field(..., description="Token count (mandatory, populated during normalization)")
    metadata: dict[str, str] = Field(default_factory=dict, description="Optional metadata")

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, v: str) -> str:
        """Validate conversation_id format."""
        if not v.startswith("c_"):
            raise ValueError(f"conversation_id must start with 'c_', got: {v}")
        # Basic validation - full validation done by conversation_id module
        return v

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Ensure content is trimmed."""
        return v.strip()

    @field_validator("ts")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        """Validate ISO-8601 timestamp format."""
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"Invalid ISO-8601 timestamp: {v}") from e
        return v


def normalize_message(
    raw_input: str | dict[str, str] | dict[str, str | list[str]],
    conversation_id: str,
    user_id: str,
    role: Literal["user", "assistant", "system"] | None = None,
) -> Message:
    """Normalize a raw message input into canonical Message schema.

    This is a pure function that:
    - Injects conversation_id and user_id from request context
    - Generates server-side timestamp (ignoring any client-provided timestamp)
    - Coerces content into a string (handles lists, objects, etc.)
    - Validates and normalizes role
    - Defaults metadata to empty dict
    - Counts tokens and attaches to message (B25)

    Token counting happens after normalization but before the message is returned.
    This ensures every message has a token count before storage or prompt use.

    Args:
        raw_input: Raw message input - can be:
            - String (content only)
            - Dict with 'content' key
            - Dict with 'role' and 'content' keys
        conversation_id: Conversation ID from request context
        user_id: User ID from request context
        role: Explicit role override. If None, extracted from raw_input if present,
              otherwise defaults based on context (user messages default to "user")

    Returns:
        Normalized Message object with tokens populated

    Raises:
        ValueError: If role is invalid, content cannot be coerced, or token count exceeds limit
    """
    # Extract role
    if role is None:
        if isinstance(raw_input, dict):
            role_raw = raw_input.get("role")
            if role_raw is not None:
                # Ensure role is a string (coerce if needed)
                if isinstance(role_raw, str):
                    role = _normalize_role(role_raw)
                else:
                    # Coerce non-string role to string
                    role = _normalize_role(str(role_raw))
            else:
                # Default to "user" if no role specified
                role = "user"
        else:
            # Default to "user" for string inputs
            role = "user"
    else:
        role = _normalize_role(role)

    # Extract and coerce content
    if isinstance(raw_input, str):
        content = raw_input
    elif isinstance(raw_input, dict):
        content_raw = raw_input.get("content")
        if content_raw is None:
            raise ValueError("Message dict must contain 'content' key")
        content = _coerce_content_to_string(content_raw)
    else:
        content = _coerce_content_to_string(raw_input)

    # Trim content
    content = content.strip()

    # Reject empty content
    if not content:
        raise ValueError("Message content cannot be empty")

    # Generate server-side timestamp (ignore any client-provided timestamp)
    ts = datetime.now(timezone.utc).isoformat()

    # Create normalized message (tokens will be populated next)
    message = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        content=content,
        ts=ts,
        tokens=0,  # Placeholder, will be set immediately
        metadata={},  # Default to empty dict
    )

    # Count tokens and attach to message (B25)
    # This happens after normalization but before message is returned
    # Token counting may raise ValueError if count exceeds safety limit
    token_count = count_tokens(
        role=role,
        content=content,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    message.tokens = token_count

    logger.debug(
        "Normalized message",
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        content_length=len(content),
        token_count=token_count,
    )

    return message


def _normalize_role(role: str) -> Literal["user", "assistant", "system"]:
    """Normalize role string to valid role.

    Args:
        role: Role string (case-insensitive)

    Returns:
        Normalized role literal

    Raises:
        ValueError: If role is not one of: user, assistant, system
    """
    role_lower = role.lower().strip()
    if role_lower in {"user", "assistant", "system"}:
        return role_lower  # type: ignore[return-value]
    raise ValueError(f"Invalid role: {role}. Must be one of: user, assistant, system")


def _coerce_content_to_string(content: str | list[str] | dict[str, str] | object) -> str:
    """Coerce content of any type into a string.

    Args:
        content: Content to coerce - can be string, list, dict, or object

    Returns:
        String representation of content
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Join list items with newlines
        return "\n".join(str(item) for item in content)
    if isinstance(content, dict):
        # Convert dict to JSON-like string representation
        return json.dumps(content, ensure_ascii=False)
    # For any other type, convert to string
    return str(content)
