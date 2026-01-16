"""PlanRevisionBuilder - constructs immutable PlanRevision records.

Used by executors to record all changes, rules, and outcomes.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from app.plans.revision.types import PlanRevision, RevisionDelta, RevisionOutcome, RevisionRule, RevisionScope


class PlanRevisionBuilder:
    """Builder for creating PlanRevision records.

    Usage:
        builder = PlanRevisionBuilder(scope="week", user_request="Reduce volume by 20%")
        builder.add_delta(entity_type="session", entity_id="s1", field="distance_mi", old=5.0, new=4.0)
        builder.add_rule(rule_id="TAPER_ONLY_REDUCTIONS", description="...", severity="block", triggered=True)
        revision = builder.finalize()
    """

    def __init__(self, *, scope: RevisionScope, user_request: str) -> None:
        """Initialize builder with scope and user request.

        Args:
            scope: Revision scope (day, week, season, race)
            user_request: Original user request text
        """
        self.revision = PlanRevision(
            revision_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            scope=scope,
            outcome="applied",
            user_request=user_request,
            reason=None,
            deltas=[],
            rules=[],
            affected_range=None,
        )

    def add_delta(
        self,
        *,
        entity_type: Literal["session", "week", "race"],
        field: str,
        entity_id: str | None = None,
        date: str | None = None,
        old: str | float | int | None = None,
        new: str | float | int | None = None,
    ) -> None:
        """Add a field change delta.

        Args:
            entity_type: Type of entity (session, week, race)
            field: Name of field that changed
            entity_id: Optional entity ID
            date: Optional date (ISO format)
            old: Old value
            new: New value
        """
        self.revision.deltas.append(
            RevisionDelta(
                entity_type=entity_type,
                entity_id=entity_id,
                date=date,
                field=field,
                old=old,
                new=new,
            )
        )

    def add_rule(
        self,
        *,
        rule_id: str,
        description: str,
        severity: Literal["info", "warning", "block"],
        triggered: bool,
    ) -> None:
        """Add a rule that was checked.

        Args:
            rule_id: Unique rule identifier
            description: Human-readable description
            severity: Rule severity (info, warning, block)
            triggered: Whether rule was triggered
        """
        self.revision.rules.append(
            RevisionRule(
                rule_id=rule_id,
                description=description,
                severity=severity,
                triggered=triggered,
            )
        )
        # If a blocking rule is triggered, set outcome to blocked
        if triggered and severity == "block":
            self.revision.outcome = "blocked"

    def set_range(self, start: str, end: str) -> None:
        """Set the affected date range.

        Args:
            start: Start date (ISO format)
            end: End date (ISO format)
        """
        self.revision.affected_range = {"start": start, "end": end}

    def set_reason(self, reason: str | None) -> None:
        """Set the modification reason.

        Args:
            reason: Reason for modification
        """
        self.revision.reason = reason

    def finalize(self) -> PlanRevision:
        """Finalize and return the PlanRevision.

        Automatically sets outcome to "partially_applied" if warnings are present
        (unless already blocked).

        Returns:
            Immutable PlanRevision record
        """
        # If not blocked and warnings present, set to partially_applied
        if self.revision.outcome != "blocked" and any(
            r.triggered and r.severity == "warning" for r in self.revision.rules
        ):
            self.revision.outcome = "partially_applied"

        return self.revision
