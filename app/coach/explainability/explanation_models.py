"""Explanation output models for plan revisions.

These models define the structured output format for human-readable explanations
of plan modifications, regenerations, and blocked actions.
"""

from typing import Literal, Optional

from pydantic import BaseModel


class RevisionExplanation(BaseModel):
    """Human-readable explanation of a plan revision.

    This model maps directly to UI components:
    - summary: Headline/title
    - rationale: Expandable detailed explanation
    - safeguards: List of badges to display
    - confidence: Footer reassurance message
    - revision_type: Type of revision for UI routing

    Attributes:
        summary: 1-2 sentence TL;DR of what changed
        rationale: Main detailed explanation of why changes were made
        safeguards: List of rules/constraints that were enforced
        confidence: Optional reassurance statement (e.g., "This change is safe")
        revision_type: Type of revision (MODIFY, REGENERATE, ROLLBACK, BLOCKED)
    """

    summary: str
    rationale: str
    safeguards: list[str]
    confidence: str | None = None
    revision_type: Literal["MODIFY", "REGENERATE", "ROLLBACK", "BLOCKED"]
