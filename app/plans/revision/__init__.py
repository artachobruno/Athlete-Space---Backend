"""PlanRevision - canonical truth for plan modifications."""

from app.plans.revision.builder import PlanRevisionBuilder
from app.plans.revision.explanation_payload import build_explanation_payload
from app.plans.revision.registry import PlanRevisionRegistry
from app.plans.revision.serializers import deserialize_revision, serialize_revision
from app.plans.revision.types import PlanRevision, RevisionDelta, RevisionOutcome, RevisionRule, RevisionScope

__all__ = [
    "PlanRevision",
    "PlanRevisionBuilder",
    "PlanRevisionRegistry",
    "RevisionDelta",
    "RevisionOutcome",
    "RevisionRule",
    "RevisionScope",
    "build_explanation_payload",
    "deserialize_revision",
    "serialize_revision",
]
