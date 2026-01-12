"""Types for persistence retry jobs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedSessionRetryJob:
    """Retry job for planned session persistence.

    Attributes:
        plan_id: Plan identifier (UUID as string)
        user_id: User ID (Clerk)
        session_ids: List of session IDs to retry (as strings)
        created_at: Timestamp when job was created (Unix timestamp)
        attempts: Number of retry attempts made
    """

    plan_id: str
    user_id: str
    athlete_id: int
    sessions: list[dict]
    plan_type: str
    created_at: float
    attempts: int
