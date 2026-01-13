"""Workout database models.

Canonical data model for workouts so planner-generated and user-uploaded
workouts are identical downstream.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class Workout(Base):
    """Workout table - single source of truth for all workouts.

    Stores workouts from planner, uploads, or manual entry.
    All workouts share the same structure regardless of source.

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id
    - sport: Sport type (run, bike, swim)
    - source: Source system (planner, upload, manual)
    - source_ref: Optional reference to source system (e.g., template ID, file name)
    - total_duration_seconds: Total workout duration (nullable)
    - total_distance_meters: Total workout distance (nullable)
    - status: Workout status (matched, analyzed, failed, parse_failed)
    - activity_id: Foreign key to activities.id (for matched workouts)
    - planned_session_id: Foreign key to planned_sessions.id (for matched workouts)
    - raw_notes: Original notes from user input (for auditability)
    - llm_output_json: LLM-generated structured workout JSON (for reproducibility)
    - parse_status: Parse status (success, parse_failed)
    - created_at: Record creation timestamp
    """

    __tablename__ = "workouts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    total_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_distance_meters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="matched")
    activity_id: Mapped[str | None] = mapped_column(String, ForeignKey("activities.id"), nullable=True, index=True)
    planned_session_id: Mapped[str | None] = mapped_column(String, ForeignKey("planned_sessions.id"), nullable=True, index=True)
    raw_notes: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Original notes from user input")
    llm_output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="LLM-generated structured workout JSON")
    parse_status: Mapped[str | None] = mapped_column(String, nullable=True, comment="Parse status: success, parse_failed")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    steps: Mapped[list[WorkoutStep]] = relationship("WorkoutStep", back_populates="workout")


class WorkoutStep(Base):
    """Workout step table - individual steps within a workout.

    Each workout consists of one or more ordered steps.
    Steps define the structure and targets for the workout.

    Schema:
    - id: UUID primary key
    - workout_id: Foreign key to workouts.id
    - order: Step order within workout (0-indexed or 1-indexed, must be contiguous)
    - type: Step type (warmup, steady, interval, recovery, cooldown, free)
    - duration_seconds: Step duration (nullable, but either duration or distance required)
    - distance_meters: Step distance (nullable, but either duration or distance required)
    - target_metric: Target metric type (pace, hr, power, rpe)
    - target_min: Minimum target value (nullable)
    - target_max: Maximum target value (nullable)
    - target_value: Single target value (nullable)
    - intensity_zone: Intensity zone (nullable)
    - instructions: Step instructions (nullable)
    - purpose: Step purpose/description (nullable)
    - inferred: Whether step was inferred vs explicitly defined (default: False)

    Rules:
    - Either duration_seconds OR distance_meters must be set
    - Steps are ordered and contiguous
    - "Free run" still has one step
    """

    __tablename__ = "workout_steps"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    workout_id: Mapped[str] = mapped_column(String, ForeignKey("workouts.id"), nullable=False, index=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_meters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_metric: Mapped[str | None] = mapped_column(String, nullable=True)
    target_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    intensity_zone: Mapped[str | None] = mapped_column(String, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    inferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    workout: Mapped[Workout] = relationship("Workout", back_populates="steps")


class WorkoutExport(Base):
    """Workout export table - tracks export generation for workouts.

    Stores export requests and their status for downloading workout files
    in various formats (FIT, etc.).

    Schema:
    - id: UUID primary key
    - workout_id: Foreign key to workouts.id
    - export_type: Export format type (e.g., "fit")
    - status: Export status (queued, building, ready, failed)
    - file_path: Path to generated file (nullable until ready)
    - error_message: Error message if export failed (nullable)
    - created_at: Record creation timestamp
    """

    __tablename__ = "workout_exports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    workout_id: Mapped[str] = mapped_column(String, ForeignKey("workouts.id"), nullable=False, index=True)
    export_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
