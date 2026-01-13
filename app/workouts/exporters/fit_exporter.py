"""FIT file exporter for Garmin-compatible workout files.

Converts Workout + Steps into a Garmin FIT workout file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import ClassVar

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.workout_message import WorkoutMessage
from fit_tool.profile.messages.workout_step_message import WorkoutStepMessage
from fit_tool.profile.profile_type import (
    FileType,
    Intensity,
    Manufacturer,
    Sport,
    WorkoutStepDuration,
    WorkoutStepTarget,
)
from garmin_fit_sdk import Decoder
from loguru import logger

from app.workouts.exporters.base import WorkoutExporter
from app.workouts.models import Workout, WorkoutStep


class FitWorkoutExporter(WorkoutExporter):
    """FIT file exporter for Garmin workouts."""

    export_type = "fit"

    # Sport type mapping
    SPORT_MAP: ClassVar[dict[str, Sport]] = {
        "run": Sport.RUNNING,
        "running": Sport.RUNNING,
        "bike": Sport.CYCLING,
        "cycling": Sport.CYCLING,
        "ride": Sport.CYCLING,
        "swim": Sport.SWIMMING,
        "swimming": Sport.SWIMMING,
    }

    # Step type to intensity mapping
    INTENSITY_MAP: ClassVar[dict[str, Intensity]] = {
        "warmup": Intensity.WARMUP,
        "steady": Intensity.ACTIVE,
        "interval": Intensity.ACTIVE,
        "recovery": Intensity.REST,
        "cooldown": Intensity.COOLDOWN,
        "free": Intensity.ACTIVE,
    }

    # Target metric to FIT target type mapping
    TARGET_METRIC_MAP: ClassVar[dict[str, WorkoutStepTarget]] = {
        "pace": WorkoutStepTarget.SPEED,
        "hr": WorkoutStepTarget.HEART_RATE,
        "power": WorkoutStepTarget.POWER,
        "rpe": WorkoutStepTarget.OPEN,  # RPE not directly supported
    }

    def build(self, workout: Workout, steps: list[WorkoutStep]) -> bytes:
        """Build FIT workout file from workout and steps.

        Args:
            workout: Workout model instance
            steps: List of WorkoutStep instances (ordered by step.order)

        Returns:
            FIT file data as bytes

        Raises:
            ValueError: If workout cannot be exported (e.g., distance-based steps)
        """
        # Validate steps
        if not steps:
            raise ValueError("Workout must have at least one step")

        # Sort steps by order to ensure correct sequence
        sorted_steps = sorted(steps, key=lambda s: s.order)

        # Check for distance-based steps (not supported in MVP)
        for step in sorted_steps:
            if step.distance_meters is not None and step.duration_seconds is None:
                raise ValueError(
                    f"Distance-based steps are not supported. Step {step.id} has distance_meters but no duration_seconds"
                )

        # Create FIT file builder
        builder = FitFileBuilder(auto_define=True, min_string_size=50)

        # Create File ID message
        # FIT epoch is UTC 00:00:00 December 31, 1989
        fit_epoch = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        time_since_fit_epoch = (now - fit_epoch).total_seconds()

        file_id_message = FileIdMessage()
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
        workout_msg = WorkoutMessage()
        workout_msg.sport = fit_sport
        workout_msg.num_valid_steps = len(sorted_steps)
        if workout.source_ref:
            workout_msg.workout_name = workout.source_ref[:15]  # FIT limit is 15 chars
        builder.add(workout_msg)

        # Create workout steps
        for step_idx, step in enumerate(sorted_steps):
            step_msg = WorkoutStepMessage()
            step_msg.message_index = step_idx

            # Set duration
            if step.duration_seconds is not None:
                step_msg.duration_type = WorkoutStepDuration.TIME
                step_msg.duration_time = float(step.duration_seconds)
            elif step.distance_meters is not None:
                # Distance-based (should not reach here due to validation, but handle gracefully)
                step_msg.duration_type = WorkoutStepDuration.DISTANCE
                step_msg.duration_distance = float(step.distance_meters)
            else:
                raise ValueError(f"Step {step.id} must have either duration_seconds or distance_meters")

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

            # Set notes/instructions if available
            if step.instructions:
                # FIT workout step notes are limited, truncate if needed
                notes = step.instructions[:50]  # Reasonable limit
                step_msg.workout_step_name = notes

            builder.add(step_msg)

        # Build FIT file
        fit_file = builder.build()

        # Get bytes
        fit_bytes = fit_file.to_bytes()

        # Validate the generated FIT file by attempting to decode it
        try:
            decoder = Decoder(BytesIO(fit_bytes))
            decoder.read()
            logger.debug(f"Generated FIT file validated successfully ({len(fit_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"Generated FIT file failed validation: {e}, but returning anyway")

        return fit_bytes
