"""Base exporter abstraction for workout exports.

Provides a common interface for all workout export formats.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.workouts.models import Workout, WorkoutStep


class WorkoutExporter(ABC):
    """Base class for workout exporters.

    All exporters must implement the build method to generate
    export data from a workout and its steps.
    """

    export_type: str

    @abstractmethod
    def build(self, workout: Workout, steps: list[WorkoutStep]) -> bytes:
        """Build export data from workout and steps.

        Args:
            workout: Workout model instance
            steps: List of WorkoutStep instances (ordered by step.order)

        Returns:
            Export data as bytes

        Raises:
            ValueError: If export cannot be built (e.g., invalid data)
            Exception: Other errors during export generation
        """
        raise NotImplementedError
