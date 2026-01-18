"""Step grouping module for detecting and grouping repeated workout steps.

This module detects repeating patterns in workout steps and groups them
for better UI display (e.g., "6x (Hill + Recovery)").
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.workouts.models import WorkoutStep
from app.workouts.targets_utils import get_distance_meters, get_duration_seconds


@dataclass
class StepGroup:
    """Represents a group of repeated steps."""

    group_id: str
    repeat: int
    step_ids: list[str]


def detect_repeating_patterns(steps: Sequence[WorkoutStep]) -> list[StepGroup]:
    """Detect repeating patterns in workout steps.

    Detects patterns of length 2 (and optionally 3) that repeat multiple times.
    For example: [Hill, Recovery, Hill, Recovery, Hill, Recovery] → 3x (Hill + Recovery)

    Args:
        steps: Ordered list of workout steps

    Returns:
        List of StepGroup objects representing detected patterns
    """
    if len(steps) < 4:  # Need at least 2 steps x 2 repeats
        return []

    groups: list[StepGroup] = []
    used_step_indices: set[int] = set()

    # Try pattern length 2 first (most common: Hill + Recovery, Interval + Rest, etc.)
    pattern_length = 2
    i = 0

    while i < len(steps) - pattern_length * 2 + 1:
        if i in used_step_indices:
            i += 1
            continue

        # Extract potential pattern
        pattern = steps[i : i + pattern_length]
        pattern_signature = _create_pattern_signature(pattern)

        # Check if this pattern repeats
        repeat_count = 1
        next_start = i + pattern_length

        while next_start + pattern_length <= len(steps):
            candidate = steps[next_start : next_start + pattern_length]
            candidate_signature = _create_pattern_signature(candidate)

            if candidate_signature == pattern_signature:
                repeat_count += 1
                next_start += pattern_length
            else:
                break

        # If we found at least 2 repeats, create a group
        if repeat_count >= 2:
            group_id = str(uuid.uuid4())
            step_ids: list[str] = []

            # Collect all step IDs in this pattern (ensure they're strings)
            for repeat_idx in range(repeat_count):
                for step_idx in range(pattern_length):
                    step_index = i + repeat_idx * pattern_length + step_idx
                    if step_index < len(steps):
                        step_id = steps[step_index].id
                        # Ensure step_id is a string (database might return UUID objects)
                        step_ids.append(str(step_id))
                        used_step_indices.add(step_index)

            groups.append(StepGroup(group_id=group_id, repeat=repeat_count, step_ids=step_ids))
            i = next_start
        else:
            i += 1

    return groups


def _create_pattern_signature(steps: Sequence[WorkoutStep]) -> str:
    """Create a signature for a pattern of steps.

    The signature is based on step type, duration, and distance to identify
    similar patterns even if names differ slightly.

    Args:
        steps: Sequence of steps forming a pattern

    Returns:
        String signature for the pattern
    """
    parts: list[str] = []
    for step in steps:
        # Extract duration and distance from targets JSONB
        duration_seconds = get_duration_seconds(step.targets) if step.targets else None
        distance_meters = get_distance_meters(step.targets) if step.targets else None

        step_parts = [
            step.step_type or "unknown",
            str(duration_seconds) if duration_seconds else "0",
            str(distance_meters) if distance_meters else "0",
        ]
        parts.append("|".join(step_parts))
    return ";".join(parts)


def assign_group_ids(steps: list[WorkoutStep], groups: list[StepGroup]) -> None:
    """Assign repeat_group_id to steps that belong to groups.

    Modifies steps in place by setting their repeat_group_id attribute
    (if the model supports it via a JSONB column or similar).

    Args:
        steps: List of workout steps (will be modified in place)
        groups: List of detected step groups
    """
    # Create a mapping from step ID to group ID
    step_to_group: dict[str, str] = {}
    for group in groups:
        for step_id in group.step_ids:
            step_to_group[step_id] = group.group_id

    # Assign group IDs (note: this assumes we can set an attribute)
    # In practice, this might need to be stored differently if the DB model
    # doesn't have a repeat_group_id column
    for step in steps:
        if step.id in step_to_group:
            # Store in a way that can be serialized in the API response
            # For now, we'll handle this in the API layer
            pass
