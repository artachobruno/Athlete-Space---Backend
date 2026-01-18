"""Utility functions for workout step normalization.

This module provides functions to ensure workout steps have proper names
and are normalized for display and export.
"""

from __future__ import annotations

from app.workouts.models import WorkoutStep
from app.workouts.targets_utils import get_duration_seconds


def infer_step_name(step: WorkoutStep, raw_text: str | None = None) -> str:
    """Infer step name from step attributes if name is missing.

    Uses step type, purpose, instructions, and raw text
    to infer a meaningful name for the step.

    Args:
        step: WorkoutStep model instance
        raw_text: Optional raw text from workout notes for context

    Returns:
        Inferred step name string
    """
    # Check if step already has a name in purpose or instructions
    if step.purpose:
        return step.purpose
    if step.instructions:
        return step.instructions

    # Extract step_type from database model (schema v2: step_type, not type)
    step_type = (step.step_type or "").lower()
    raw_lower = (raw_text or "").lower() if raw_text else ""

    # Extract duration from targets JSONB if needed
    targets = step.targets or {}
    duration_seconds = get_duration_seconds(targets)

    # Check for hill keywords in raw text or type
    hill_keywords = ["hill", "uphill", "climb", "gradient"]
    if any(keyword in step_type for keyword in hill_keywords) or any(keyword in raw_lower for keyword in hill_keywords):
        return "Hill"

    # Check for intent keywords in type
    if "warmup" in step_type or "warm" in step_type:
        return "Warmup"
    if "cooldown" in step_type or "cool" in step_type:
        return "Cooldown"
    if "recovery" in step_type or "recover" in step_type:
        return "Recovery"

    # Check step type for common patterns
    if step_type == "interval":
        return "Interval"
    if step_type == "steady":
        return "Steady"
    if step_type == "free":
        return "Free Run"
    if step_type == "tempo":
        return "Tempo"
    if step_type == "threshold":
        return "Threshold"
    if step_type in {"easy", "recovery", "rest"}:
        # Check if it's a short duration recovery
        if duration_seconds and duration_seconds < 300:  # Less than 5 minutes
            return "Recovery"
        return "Easy"

    # Default fallback based on type
    if step_type:
        return step_type.capitalize()

    return "Step"
