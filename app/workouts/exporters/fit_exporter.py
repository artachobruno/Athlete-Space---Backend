"""FIT file exporter for Garmin-compatible workout files.

Converts Workout + Steps into a Garmin FIT workout file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import ClassVar, cast

from garmin_fit_sdk import Decoder
from loguru import logger

from app.workouts.exporters.base import WorkoutExporter
from app.workouts.models import Workout, WorkoutStep


# Define stub classes first (for type checking when fit_tool is not available)
class _FitFileBuilderStub:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def add(self, *args: object) -> None:
        pass

    @staticmethod
    def build() -> object:
        return object()


class _FileIdMessageStub:
    type: object = None
    manufacturer: object = None
    product: int = 0
    time_created: int = 0
    serial_number: int = 0


class _WorkoutMessageStub:
    sport: object = None
    num_valid_steps: int = 0
    workout_name: str = ""


class _WorkoutStepMessageStub:
    message_index: int = 0
    duration_type: object = None
    duration_time: float = 0.0
    duration_distance: float = 0.0
    intensity: object = None
    target_type: object = None
    target_hr_zone: object = None
    target_power_zone: object = None
    custom_target_value_low: int = 0
    custom_target_value_high: int = 0
    workout_step_name: str = ""


class _FileTypeStub:
    WORKOUT: object = None


class _IntensityStub:
    WARMUP: object = None
    ACTIVE: object = None
    REST: object = None
    COOLDOWN: object = None


class _ManufacturerStubInner:
    value: int = 0


class _ManufacturerStub:
    DEVELOPMENT: _ManufacturerStubInner = _ManufacturerStubInner()


class _SportStub:
    RUNNING: object = None
    CYCLING: object = None
    SWIMMING: object = None


class _WorkoutStepDurationStub:
    TIME: object = None
    DISTANCE: object = None


class _WorkoutStepTargetStub:
    SPEED: object = None
    HEART_RATE: object = None
    POWER: object = None
    OPEN: object = None


try:
    from fit_tool.fit_file_builder import FitFileBuilder as _RealFitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage as _RealFileIdMessage
    from fit_tool.profile.messages.workout_message import WorkoutMessage as _RealWorkoutMessage
    from fit_tool.profile.messages.workout_step_message import WorkoutStepMessage as _RealWorkoutStepMessage
    from fit_tool.profile.profile_type import (
        FileType as _RealFileType,
    )
    from fit_tool.profile.profile_type import (
        Intensity as _RealIntensity,
    )
    from fit_tool.profile.profile_type import (
        Manufacturer as _RealManufacturer,
    )
    from fit_tool.profile.profile_type import (
        Sport as _RealSport,
    )
    from fit_tool.profile.profile_type import (
        WorkoutStepDuration as _RealWorkoutStepDuration,
    )
    from fit_tool.profile.profile_type import (
        WorkoutStepTarget as _RealWorkoutStepTarget,
    )
    # Assign to module-level names for use throughout the file
    FitFileBuilder = _RealFitFileBuilder
    FileIdMessage = _RealFileIdMessage
    WorkoutMessage = _RealWorkoutMessage
    WorkoutStepMessage = _RealWorkoutStepMessage
    FileType = _RealFileType
    Intensity = _RealIntensity
    Manufacturer = _RealManufacturer
    Sport = _RealSport
    WorkoutStepDuration = _RealWorkoutStepDuration
    WorkoutStepTarget = _RealWorkoutStepTarget
    FIT_TOOL_AVAILABLE = True
except ImportError:
    FIT_TOOL_AVAILABLE = False
    # Use stub classes when fit-tool is not available
    FitFileBuilder = _FitFileBuilderStub
    FileIdMessage = _FileIdMessageStub
    WorkoutMessage = _WorkoutMessageStub
    WorkoutStepMessage = _WorkoutStepMessageStub
    FileType = _FileTypeStub
    Intensity = _IntensityStub
    Manufacturer = _ManufacturerStub
    Sport = _SportStub
    WorkoutStepDuration = _WorkoutStepDurationStub
    WorkoutStepTarget = _WorkoutStepTargetStub


class FitWorkoutExporter(WorkoutExporter):
    """FIT file exporter for Garmin workouts."""

    export_type = "fit"

    # Sport type mapping
    SPORT_MAP: ClassVar[dict[str, object]] = {
        "run": Sport.RUNNING,
        "running": Sport.RUNNING,
        "bike": Sport.CYCLING,
        "cycling": Sport.CYCLING,
        "ride": Sport.CYCLING,
        "swim": Sport.SWIMMING,
        "swimming": Sport.SWIMMING,
    }

    # Step type to intensity mapping
    INTENSITY_MAP: ClassVar[dict[str, object]] = {
        "warmup": Intensity.WARMUP,
        "steady": Intensity.ACTIVE,
        "interval": Intensity.ACTIVE,
        "recovery": Intensity.REST,
        "cooldown": Intensity.COOLDOWN,
        "free": Intensity.ACTIVE,
    }

    # Target metric to FIT target type mapping
    TARGET_METRIC_MAP: ClassVar[dict[str, object]] = {
        "pace": WorkoutStepTarget.SPEED,
        "hr": WorkoutStepTarget.HEART_RATE,
        "power": WorkoutStepTarget.POWER,
        "rpe": WorkoutStepTarget.OPEN,  # RPE not directly supported
    }

    def build(self, workout: Workout, steps: list[WorkoutStep]) -> bytes:
        """Build FIT workout file from workout and steps.

        Args:
            workout: Workout model instance
            steps: List of WorkoutStep instances (ordered by step.step_index)

        Returns:
            FIT file data as bytes

        Raises:
            ValueError: If workout cannot be exported (e.g., distance-based steps)
            ImportError: If fit-tool package is not installed
        """
        if not FIT_TOOL_AVAILABLE:
            raise ImportError(
                "fit-tool package is not installed. "
                "Install it with: pip install fit-tool "
                "or from GitHub if available."
            )
        # After the check above, we know FIT_TOOL_AVAILABLE is True
        # This means we're using real fit_tool types, not stubs
        # Validate steps
        if not steps:
            raise ValueError("Workout must have at least one step")

        # Sort steps by step_index to ensure correct sequence
        sorted_steps = sorted(steps, key=lambda s: s.step_index)

        # Filter out invalid steps and warn
        valid_steps: list[WorkoutStep] = []
        for step in sorted_steps:
            # Skip steps with neither distance nor duration
            if step.distance_meters is None and step.duration_seconds is None:
                logger.warning(
                    f"Skipping step {step.id} (order {step.step_index}): missing both distance_meters and duration_seconds"
                )
                continue

            # Check for distance-based steps (not supported in MVP, but handle gracefully)
            if step.distance_meters is not None and step.duration_seconds is None:
                logger.warning(
                    f"Step {step.id} (order {step.step_index}) is distance-based only, which may not be fully supported"
                )
                # Continue processing - will be handled in step creation

            valid_steps.append(step)

        if not valid_steps:
            raise ValueError("Workout has no valid steps after filtering")

        # Update sorted_steps to use filtered list
        sorted_steps = valid_steps

        # Create FIT file builder
        # After FIT_TOOL_AVAILABLE check, we know we're using real types
        # Use cast to help pyright understand we're using real types (not stubs)
        builder = cast(type[_RealFitFileBuilder], FitFileBuilder)(auto_define=True, min_string_size=50)

        # Create File ID message
        # FIT epoch is UTC 00:00:00 December 31, 1989
        fit_epoch = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        time_since_fit_epoch = (now - fit_epoch).total_seconds()

        file_id_message = cast(type[_RealFileIdMessage], FileIdMessage)()
        file_id_message.type = FileType.WORKOUT
        file_id_message.manufacturer = Manufacturer.DEVELOPMENT.value
        file_id_message.product = 0
        file_id_message.time_created = round(time_since_fit_epoch)
        file_id_message.serial_number = 0x12345678
        builder.add(file_id_message)

        # Map sport type
        sport_lower = workout.sport.lower()
        fit_sport = self.SPORT_MAP.get(sport_lower, Sport.RUNNING)

        # Create workout message
        workout_msg = cast(type[_RealWorkoutMessage], WorkoutMessage)()
        workout_msg.sport = fit_sport
        workout_msg.num_valid_steps = len(sorted_steps)  # Use filtered list length
        if workout.source_ref:
            workout_msg.workout_name = workout.source_ref[:15]  # FIT limit is 15 chars
        builder.add(workout_msg)

        # Create workout steps
        for step_idx, step in enumerate(sorted_steps):
            step_msg = cast(type[_RealWorkoutStepMessage], WorkoutStepMessage)()
            step_msg.message_index = step_idx

            # Set duration
            # Prefer duration_seconds if both are present
            if step.duration_seconds is not None:
                step_msg.duration_type = WorkoutStepDuration.TIME
                step_msg.duration_time = float(step.duration_seconds)
            elif step.distance_meters is not None:
                # Distance-based (duration-only workouts are preferred, but support distance-based)
                step_msg.duration_type = WorkoutStepDuration.DISTANCE
                step_msg.duration_distance = float(step.distance_meters)
            else:
                # This should not happen due to filtering above, but handle gracefully
                logger.warning(f"Skipping step {step.id} (order {step.step_index}): missing both distance and duration")
                continue

            # Set intensity from step type
            step_type_lower = step.type.lower() if step.type else "steady"
            step_msg.intensity = self.INTENSITY_MAP.get(step_type_lower, Intensity.ACTIVE)

            # Set target
            if step.target_metric:
                target_metric_lower = step.target_metric.lower()
                step_msg.target_type = self.TARGET_METRIC_MAP.get(target_metric_lower, WorkoutStepTarget.OPEN)

                # Set target values based on metric type
                if target_metric_lower == "hr":
                    # Heart rate targets
                    if step.target_min is not None:
                        step_msg.target_hr_zone = None  # Use custom value
                        step_msg.custom_target_value_low = int(step.target_min)
                    if step.target_max is not None:
                        step_msg.custom_target_value_high = int(step.target_max)
                    elif step.target_value is not None:
                        step_msg.custom_target_value_low = int(step.target_value)
                        step_msg.custom_target_value_high = int(step.target_value)
                elif target_metric_lower == "power":
                    # Power targets
                    if step.target_min is not None:
                        step_msg.target_power_zone = None  # Use custom value
                        step_msg.custom_target_value_low = int(step.target_min)
                    if step.target_max is not None:
                        step_msg.custom_target_value_high = int(step.target_max)
                    elif step.target_value is not None:
                        step_msg.custom_target_value_low = int(step.target_value)
                        step_msg.custom_target_value_high = int(step.target_value)
                else:
                    # Speed/pace or other targets
                    if step.target_min is not None:
                        step_msg.custom_target_value_low = int(step.target_min)
                    if step.target_max is not None:
                        step_msg.custom_target_value_high = int(step.target_max)
                    elif step.target_value is not None:
                        step_msg.custom_target_value_low = int(step.target_value)
                        step_msg.custom_target_value_high = int(step.target_value)
            else:
                # No target specified - open step
                step_msg.target_type = WorkoutStepTarget.OPEN

            # Set step name/description - prefer purpose, then instructions
            step_name = step.purpose or step.instructions
            if step_name:
                # FIT workout step names are limited, truncate if needed
                step_msg.workout_step_name = step_name[:50]  # Reasonable limit

            # Type narrowing ensures we're using real types here
            builder.add(step_msg)

        # Build FIT file
        fit_file = builder.build()

        # Get bytes
        fit_bytes = fit_file.to_bytes()

        # Validate the generated FIT file by attempting to decode it
        try:
            decoder = Decoder(BytesIO(fit_bytes))  # type: ignore[arg-type]
            decoder.read()
            logger.debug(f"Generated FIT file validated successfully ({len(fit_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"Generated FIT file failed validation: {e}, but returning anyway")

        return fit_bytes
