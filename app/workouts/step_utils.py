"""Utility functions for workout step normalization.

This module provides functions to ensure workout steps have proper names
and are normalized for display and export.
"""

from __future__ import annotations

from app.workouts.models import WorkoutStep


def infer_step_name(step: WorkoutStep) -> str:
    """Infer step name from step attributes if name is missing.

    Uses step type, intensity_zone, purpose, and instructions to infer
    a meaningful name for the step.

    Args:
        step: WorkoutStep model instance

    Returns:
        Inferred step name string
    """
    # Check if step already has a name in purpose or instructions
    if step.purpose:
        return step.purpose
    if step.instructions:
        return step.instructions

    # Infer from type and intensity
    step_type = (step.type or "").lower()
    intensity = (step.intensity_zone or "").lower()

    # Check for intent keywords in type
    if "warmup" in step_type or "warm" in step_type:
        return "Warmup"
    if "cooldown" in step_type or "cool" in step_type:
        return "Cooldown"
    if "recovery" in step_type or "recover" in step_type:
        return "Recovery"

    # Check intensity for common patterns
    if intensity == "warmup" or "warmup" in intensity:
        return "Warmup"
    if intensity == "cooldown" or "cooldown" in intensity:
        return "Cooldown"
    if intensity in {"tempo", "threshold", "lt2"}:
        return intensity.capitalize()
    if intensity == "hill":
        return "Hill Repeats"
    if intensity in {"easy", "recovery", "rest"}:
        return "Recovery"
    if intensity == "vo2":
        return "VO2 Intervals"

    # Check step type for common patterns
    if step_type == "interval":
        return "Interval"
    if step_type == "steady":
        return "Steady"
    if step_type == "free":
        return "Free Run"

    # Default fallback
    return "Steady"
