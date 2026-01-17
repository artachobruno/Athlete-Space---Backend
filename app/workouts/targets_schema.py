"""JSONB schema for workout step targets.

This defines the structure of the `targets` JSONB column in workout_steps.
This schema is stable and allows flexible expression of duration and target intent
without requiring database migrations.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DurationTime(BaseModel):
    """Time-based duration."""

    type: Literal["time"] = "time"
    seconds: int = Field(..., description="Duration in seconds", ge=0)


class DurationDistance(BaseModel):
    """Distance-based duration."""

    type: Literal["distance"] = "distance"
    meters: int = Field(..., description="Distance in meters", ge=0)


class DurationOpen(BaseModel):
    """Open-ended duration (until lap/feel)."""

    type: Literal["open"] = "open"


Duration = DurationTime | DurationDistance | DurationOpen


class TargetSingleValue(BaseModel):
    """Single value target."""

    metric: str = Field(..., description="Metric type: pace, hr, power, rpe, zone")
    value: str | float = Field(..., description="Target value (string for pace, number for others)")
    unit: str | None = Field(None, description="Unit (e.g., 'bpm', 'w', 'km/h')")


class TargetRange(BaseModel):
    """Range target."""

    metric: str = Field(..., description="Metric type: pace, hr, power, rpe")
    min: str | float = Field(..., description="Minimum value")
    max: str | float = Field(..., description="Maximum value")
    unit: str | None = Field(None, description="Unit (e.g., 'bpm', 'w', 'km/h')")


Target = TargetSingleValue | TargetRange | None


class StepTargets(BaseModel):
    """Complete targets structure for a workout step.

    This is what gets stored in the `targets` JSONB column.
    """

    duration: Duration | None = Field(None, description="Step duration (time, distance, or open)")
    target: Target = Field(None, description="Step target (single value or range)")

    def model_dump_jsonb(self) -> dict:
        """Dump as dict suitable for JSONB storage."""
        result: dict = {}
        if self.duration:
            result["duration"] = self.duration.model_dump()
        if self.target:
            result["target"] = self.target.model_dump()
        return result

    @classmethod
    def from_legacy(
        cls,
        duration_seconds: int | None = None,
        distance_meters: int | None = None,
        target_metric: str | None = None,
        target_min: float | None = None,
        target_max: float | None = None,
        target_value: float | None = None,
    ) -> StepTargets:
        """Create StepTargets from legacy individual columns.

        This is used during migration from old schema to new schema.
        """
        # Build duration
        duration: Duration | None = None
        if duration_seconds is not None:
            duration = DurationTime(seconds=duration_seconds)
        elif distance_meters is not None:
            duration = DurationDistance(meters=distance_meters)

        # Build target
        target: Target = None
        if target_metric:
            if target_min is not None and target_max is not None:
                target = TargetRange(metric=target_metric, min=target_min, max=target_max)
            elif target_value is not None:
                target = TargetSingleValue(metric=target_metric, value=target_value)

        return cls(duration=duration, target=target)

    def to_legacy(self) -> dict[str, int | float | str | None]:
        """Convert to legacy format for backward compatibility.

        Returns dict with keys: duration_seconds, distance_meters, target_metric,
        target_min, target_max, target_value.
        """
        result: dict[str, int | float | str | None] = {
            "duration_seconds": None,
            "distance_meters": None,
            "target_metric": None,
            "target_min": None,
            "target_max": None,
            "target_value": None,
        }

        # Extract duration
        if self.duration:
            if isinstance(self.duration, DurationTime):
                result["duration_seconds"] = self.duration.seconds
            elif isinstance(self.duration, DurationDistance):
                result["distance_meters"] = self.duration.meters

        # Extract target
        if self.target:
            result["target_metric"] = self.target.metric
            if isinstance(self.target, TargetRange):
                result["target_min"] = self.target.min if isinstance(self.target.min, (int, float)) else None
                result["target_max"] = self.target.max if isinstance(self.target.max, (int, float)) else None
            elif isinstance(self.target, TargetSingleValue):
                result["target_value"] = self.target.value if isinstance(self.target.value, (int, float)) else None

        return result
