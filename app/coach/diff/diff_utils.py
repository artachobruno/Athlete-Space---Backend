"""Utility functions for diff operations."""

from typing import Any

IGNORED_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "revision_id",
    "_sa_instance_state",  # SQLAlchemy internal state
}


def comparable_dict(session) -> dict[str, Any]:
    """Extract comparable fields from a PlannedSession.

    Removes metadata fields that should not be compared (id, timestamps, etc.).

    Args:
        session: PlannedSession object (or any object with __dict__)

    Returns:
        Dictionary of comparable fields
    """
    return {k: v for k, v in session.__dict__.items() if k not in IGNORED_FIELDS}
