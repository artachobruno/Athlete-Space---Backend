"""FIT file exporter for Garmin-compatible workout files.

Converts Workout + Steps into a Garmin FIT workout file.
"""

from __future__ import annotations

from io import BytesIO
from typing import ClassVar

from garmin_fit_sdk import Decoder, Encoder, Profile, Stream
from loguru import logger

from app.workouts.exporters.base import WorkoutExporter
from app.workouts.models import Workout, WorkoutStep


class FitWorkoutExporter(WorkoutExporter):
    """FIT file exporter for Garmin workouts."""

    export_type = "fit"

    # Sport type mapping
    SPORT_MAP: ClassVar[dict[str, Profile.Sport]] = {
        "run": Profile.Sport.running,
        "running": Profile.Sport.running,
        "bike": Profile.Sport.cycling,
        "cycling": Profile.Sport.cycling,
        "ride": Profile.Sport.cycling,
        "swim": Profile.Sport.swimming,
        "swimming": Profile.Sport.swimming,
    }

    # Step type to intensity mapping
    INTENSITY_MAP: ClassVar[dict[str, Profile.Intensity]] = {
        "warmup": Profile.Intensity.warmup,
        "steady": Profile.Intensity.active,
        "interval": Profile.Intensity.active,
        "recovery": Profile.Intensity.rest,
        "cooldown": Profile.Intensity.cooldown,
        "free": Profile.Intensity.active,
    }

    # Target metric to FIT target type mapping
    TARGET_METRIC_MAP: ClassVar[dict[str, Profile.WorkoutStepTargetType]] = {
        "pace": Profile.WorkoutStepTargetType.speed,
        "hr": Profile.WorkoutStepTargetType.heart_rate,
        "power": Profile.WorkoutStepTargetType.power,
        "rpe": Profile.WorkoutStepTargetType.open,  # RPE not directly supported
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

        # Create FIT file stream
        stream = Stream()
        encoder = Encoder(stream)

        # Map sport type
        sport_lower = workout.sport.lower()
        fit_sport = self.SPORT_MAP.get(sport_lower, Profile.Sport.running)

        # Create workout message
        workout_msg = Profile.WorkoutMessage()
        workout_msg.sport = fit_sport
        workout_msg.num_valid_steps = len(sorted_steps)
        if workout.source_ref:
            workout_msg.wkt_name = workout.source_ref[:15]  # FIT limit is 15 chars
        encoder.write(workout_msg)

        # Create workout steps
        for step_idx, step in enumerate(sorted_steps):
            step_msg = Profile.WorkoutStepMessage()
            step_msg.message_index = Profile.MessageIndex()
            step_msg.message_index.value = step_idx

            # Set duration
            if step.duration_seconds is not None:
                step_msg.duration_type = Profile.WorkoutStepDurationType.time
                step_msg.duration_value = step.duration_seconds
            elif step.distance_meters is not None:
                # Distance-based (should not reach here due to validation, but handle gracefully)
                step_msg.duration_type = Profile.WorkoutStepDurationType.distance
                step_msg.duration_value = int(step.distance_meters)
            else:
                raise ValueError(f"Step {step.id} must have either duration_seconds or distance_meters")

            # Set intensity from step type
            step_type_lower = step.type.lower() if step.type else "steady"
            step_msg.intensity = self.INTENSITY_MAP.get(step_type_lower, Profile.Intensity.active)

            # Set target
            if step.target_metric:
                target_metric_lower = step.target_metric.lower()
                step_msg.target_type = self.TARGET_METRIC_MAP.get(
                    target_metric_lower, Profile.WorkoutStepTargetType.open
                )

                # Set target values
                if step.target_min is not None:
                    step_msg.custom_target_value_low = int(step.target_min)
                if step.target_max is not None:
                    step_msg.custom_target_value_high = int(step.target_max)
                elif step.target_value is not None:
                    # Use target_value as both low and high if no range specified
                    step_msg.custom_target_value_low = int(step.target_value)
                    step_msg.custom_target_value_high = int(step.target_value)
            else:
                # No target specified - open step
                step_msg.target_type = Profile.WorkoutStepTargetType.open

            # Set notes/instructions if available
            if step.instructions:
                # FIT workout step notes are limited, truncate if needed
                notes = step.instructions[:50]  # Reasonable limit
                step_msg.message = notes

            encoder.write(step_msg)

        # Finalize FIT file
        encoder.finish()

        # Get bytes
        fit_bytes = stream.getvalue()

        # Validate the generated FIT file by attempting to decode it
        try:
            decoder = Decoder(BytesIO(fit_bytes))
            decoder.read()
            logger.debug(f"Generated FIT file validated successfully ({len(fit_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"Generated FIT file failed validation: {e}, but returning anyway")

        return fit_bytes
