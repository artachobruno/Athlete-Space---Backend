"""Explainability layer for plan revisions.

This module provides human-readable explanations for:
- Plan modifications (day, week, season)
- Plan regenerations
- Blocked modifications

The explainability layer is read-only and never mutates state.
"""

from app.coach.explainability.explanation_models import RevisionExplanation
from app.coach.explainability.revision_explainer import explain_plan_revision

__all__ = ["RevisionExplanation", "explain_plan_revision"]
