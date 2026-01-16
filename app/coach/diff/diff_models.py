"""Diff models for plan revision tracking.

These models represent machine-readable, deterministic diffs between
PlannedSession objects and plans.
"""

from typing import Any, Literal

from pydantic import BaseModel


class FieldChange(BaseModel):
    """A single field change in a session."""

    field: str
    before: Any
    after: Any


class SessionFieldDiff(BaseModel):
    """Field-level changes for a modified session."""

    session_id: str
    changes: list[FieldChange]


class SessionDiff(BaseModel):
    """Summary of an added or removed session."""

    session_id: str
    date: str
    type: str
    title: str | None = None


class PlanDiff(BaseModel):
    """Complete diff between two plans.

    This is the single source of truth for:
    - Explainability
    - UI highlights
    - Audit history
    - Rollbacks (later)
    """

    scope: Literal["day", "week", "plan"]
    added: list[SessionDiff]
    removed: list[SessionDiff]
    modified: list[SessionFieldDiff]
    unchanged: list[str]  # session_ids
