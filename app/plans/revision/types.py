"""PlanRevision types - canonical truth for plan modifications.

PlanRevision answers one question only:
"What changed, why did it change, and under which rules?"

It does NOT:
- execute changes
- infer intent
- generate text
- call the LLM

It is:
- immutable
- append-only
- auditable
- explainable
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

RevisionScope = Literal["day", "week", "season", "race"]
RevisionOutcome = Literal["applied", "partially_applied", "blocked"]


class RevisionDelta(BaseModel):
    """A single field change in a plan modification.

    Attributes:
        entity_type: Type of entity being modified (session, week, race)
        entity_id: Optional ID of the entity
        date: Optional date of the entity (ISO format)
        field: Name of the field that changed
        old: Old value (before modification)
        new: New value (after modification)
    """

    entity_type: Literal["session", "week", "race"]
    entity_id: str | None = None
    date: str | None = None
    field: str
    old: str | float | int | None = None
    new: str | float | int | None = None


class RevisionRule(BaseModel):
    """A rule that was checked during modification.

    Attributes:
        rule_id: Unique identifier for the rule
        description: Human-readable description of the rule
        severity: Rule severity (info, warning, block)
        triggered: Whether the rule was triggered (blocked or warned)
    """

    rule_id: str
    description: str
    severity: Literal["info", "warning", "block"]
    triggered: bool


class PlanRevision(BaseModel):
    """Immutable record of a plan modification.

    This is the canonical truth for what changed, why, and under which rules.
    It contains NO free-text explanations, NO LLM output, NO inferred semantics.

    Attributes:
        revision_id: Unique identifier for this revision
        created_at: Timestamp when revision was created
        scope: Scope of modification (day, week, season, race)
        outcome: Outcome of modification (applied, partially_applied, blocked)
        user_request: Original user request text
        reason: Optional reason for modification
        deltas: List of field changes
        rules: List of rules that were checked
        affected_range: Optional date range affected (start, end in ISO format)
    """

    revision_id: str
    created_at: datetime

    scope: RevisionScope
    outcome: RevisionOutcome

    user_request: str
    reason: str | None = None

    deltas: list[RevisionDelta] = []
    rules: list[RevisionRule] = []

    affected_range: dict[str, str] | None = None  # {start, end}
