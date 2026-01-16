"""Serializers for PlanRevision - JSON serialization utilities."""

from app.plans.revision.types import PlanRevision


def serialize_revision(revision: PlanRevision) -> dict:
    """Serialize PlanRevision to JSON-serializable dict.

    Args:
        revision: PlanRevision to serialize

    Returns:
        JSON-serializable dictionary
    """
    return revision.model_dump(mode="json")


def deserialize_revision(data: dict) -> PlanRevision:
    """Deserialize dict to PlanRevision.

    Args:
        data: Dictionary containing revision data

    Returns:
        PlanRevision object
    """
    return PlanRevision(**data)
