"""PlanRevisionRegistry - storage-agnostic revision registry.

v1: No-op implementation (revisions are created but not persisted).
v2: Will add database persistence.
"""

from app.plans.revision.types import PlanRevision


class PlanRevisionRegistry:
    """Registry for storing PlanRevision records.

    v1: No-op implementation - revisions are created but not persisted.
    v2: Will add database persistence for audit logs and history.
    """

    def save(self, revision: PlanRevision) -> None:
        """Save a revision to the registry.

        v1: No-op (revisions are created but not persisted).
        v2: Will persist to database.

        Args:
            revision: PlanRevision to save
        """
        pass
