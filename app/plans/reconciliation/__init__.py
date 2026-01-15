"""HR-based pace reconciliation module.

This module provides conservative, explainability-first reconciliation
that compares planned workout intent vs observed HR zone.

This is observation + interpretation only - no plan mutations.
"""

from app.plans.reconciliation.hr import map_hr_to_zone
from app.plans.reconciliation.reconcile import reconcile_workout
from app.plans.reconciliation.service import reconcile_activity_if_paired
from app.plans.reconciliation.types import ExecutedWorkout, ReconciliationResult

__all__ = [
    "ExecutedWorkout",
    "ReconciliationResult",
    "map_hr_to_zone",
    "reconcile_activity_if_paired",
    "reconcile_workout",
]
