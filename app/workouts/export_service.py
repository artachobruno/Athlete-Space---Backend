"""Workout export service for generating and managing workout exports.

Handles export creation, execution, and file management.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.workouts.exporters.base import WorkoutExporter
from app.workouts.exporters.fit_exporter import FitWorkoutExporter
from app.workouts.models import Workout, WorkoutExport, WorkoutStep

# Export type to exporter class mapping
EXPORTER_REGISTRY: dict[str, type[WorkoutExporter]] = {
    "fit": FitWorkoutExporter,
}


def get_exporter(export_type: str) -> WorkoutExporter:
    """Get exporter instance for export type.

    Args:
        export_type: Export type (e.g., "fit")

    Returns:
        WorkoutExporter instance

    Raises:
        ValueError: If export_type is not supported
    """
    exporter_class = EXPORTER_REGISTRY.get(export_type)
    if exporter_class is None:
        raise ValueError(f"Unsupported export type: {export_type}")
    return exporter_class()


def load_workout_with_steps(session: Session, workout_id: str) -> tuple[Workout, list[WorkoutStep]]:
    """Load workout and its steps from database.

    Args:
        session: Database session
        workout_id: Workout UUID string

    Returns:
        Tuple of (Workout, list[WorkoutStep])

    Raises:
        ValueError: If workout not found
    """
    # Load workout
    stmt = select(Workout).where(Workout.id == workout_id)
    result = session.execute(stmt)
    workout = result.scalar_one_or_none()

    if workout is None:
        raise ValueError(f"Workout {workout_id} not found")

    # Load steps ordered by step_index
    steps_stmt = select(WorkoutStep).where(WorkoutStep.workout_id == workout_id).order_by(WorkoutStep.step_index)
    steps_result = session.execute(steps_stmt)
    steps = list(steps_result.scalars().all())

    return workout, steps


def save_binary(data: bytes, relative_path: str) -> str:
    """Save binary data to file.

    Creates the directory if it doesn't exist.

    Args:
        data: Binary data to save
        relative_path: Relative path from project root (e.g., "exports/file.fit")

    Returns:
        Absolute file path

    Raises:
        OSError: If file cannot be written
    """
    # Get project root (assuming this file is in app/workouts/)
    project_root = Path(__file__).parent.parent.parent
    file_path = project_root / relative_path

    # Create directory if it doesn't exist
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write file
    file_path.write_bytes(data)

    # Return absolute path as string
    return str(file_path.resolve())


class WorkoutExportService:
    """Service for workout export operations."""

    @staticmethod
    def create_export(db: Session, workout_id: str, export_type: str) -> WorkoutExport:
        """Create a new export record.

        Args:
            db: Database session
            workout_id: Workout UUID string
            export_type: Export type (e.g., "fit")

        Returns:
            Created WorkoutExport instance
        """
        export = WorkoutExport(
            id=str(uuid.uuid4()),
            workout_id=workout_id,
            export_type=export_type,
            status="queued",
        )
        db.add(export)
        db.flush()
        return export

    @staticmethod
    def run_export(db: Session, export_id: str) -> WorkoutExport:
        """Run export generation (inline execution).

        Loads workout and steps, generates export file, and updates status.

        Args:
            db: Database session
            export_id: Export UUID string

        Returns:
            Updated WorkoutExport instance

        Raises:
            ValueError: If export not found or workout cannot be loaded
        """
        # Load export
        stmt = select(WorkoutExport).where(WorkoutExport.id == export_id)
        result = db.execute(stmt)
        export = result.scalar_one_or_none()

        if export is None:
            raise ValueError(f"Export {export_id} not found")

        try:
            # Update status to building
            export.status = "building"
            db.flush()

            # Load workout and steps
            workout, steps = load_workout_with_steps(db, export.workout_id)

            # Get exporter
            exporter = get_exporter(export.export_type)

            # Generate export data
            logger.info(f"Generating {export.export_type} export for workout {export.workout_id}")
            data = exporter.build(workout, steps)

            # Save file
            file_path = save_binary(data, f"exports/{export.id}.{export.export_type}")

            # Update export with file path and status
            export.file_path = file_path
            export.status = "ready"
            logger.info(f"Export {export_id} completed successfully: {file_path}")

        except Exception:
            # Update export with error
            export.status = "failed"
            export.error_message = "Export generation failed"
            logger.exception(f"Export {export_id} failed")

        db.flush()
        return export

    @staticmethod
    def get_export(db: Session, export_id: str) -> WorkoutExport | None:
        """Get export by ID.

        Args:
            db: Database session
            export_id: Export UUID string

        Returns:
            WorkoutExport instance or None if not found
        """
        stmt = select(WorkoutExport).where(WorkoutExport.id == export_id)
        result = db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def list_exports(db: Session, workout_id: str) -> list[WorkoutExport]:
        """List all exports for a workout.

        Args:
            db: Database session
            workout_id: Workout UUID string

        Returns:
            List of WorkoutExport instances ordered by created_at DESC
        """
        stmt = (
            select(WorkoutExport)
            .where(WorkoutExport.workout_id == workout_id)
            .order_by(WorkoutExport.created_at.desc())
        )
        result = db.execute(stmt)
        return list(result.scalars().all())
