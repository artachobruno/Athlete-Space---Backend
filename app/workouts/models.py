"""Workout database models.

Canonical data model for workouts so planner-generated and user-uploaded
workouts are identical downstream.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class Workout(Base):
    """Workout table - template for workouts.

    Stores workout templates (intent/plan), not executions.
    Execution data belongs in workout_executions table.

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id
    - sport: Sport type (run, bike, swim)
    - name: Workout name/title
    - description: Workout description
    - structure: JSONB structure with intervals, targets, etc.
    - tags: JSONB tags
    - source: Source system (planner, upload, manual, inferred)
    - source_ref: Optional reference to source system (e.g., template ID, file name)
    - raw_notes: Original notes from user input (for auditability)
    - parse_status: Parse status (success, parse_failed, pending)
    - created_at: Record creation timestamp
    - updated_at: Record update timestamp
    """

    __tablename__ = "workouts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    structure: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_notes: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Original notes from user input")
    parse_status: Mapped[str | None] = mapped_column(String, nullable=True, comment="Parse status: success, parse_failed, pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    steps: Mapped[list[WorkoutStep]] = relationship("WorkoutStep", back_populates="workout")


class WorkoutStep(Base):
    """Workout step table - individual steps within a workout.

    Each workout consists of one or more ordered steps.
    Steps define the structure and targets for the workout.

    Schema:
    - id: UUID primary key
    - workout_id: Foreign key to workouts.id
    - step_index: Step order within workout (0-indexed or 1-indexed, must be contiguous)
    - step_type: Step type (warmup, steady, interval, recovery, cooldown, free)
    - targets: JSONB containing duration and target definition (see targets_schema.py)
    - instructions: Step instructions (nullable, free text)
    - purpose: Step purpose/description (nullable, semantic label)

    The `targets` JSONB structure:
    {
      "duration": {
        "type": "time" | "distance" | "open",
        "seconds": <int> (if type="time"),
        "meters": <int> (if type="distance")
      },
      "target": {
        "metric": "pace" | "hr" | "power" | "rpe" | "zone",
        "value": <str|float> (if single value),
        "min": <str|float>, "max": <str|float> (if range),
        "unit": <str> (optional)
      }
    }

    Rules:
    - targets.duration must specify either time or distance (or open)
    - Steps are ordered and contiguous
    - "Free run" still has one step
    """

    __tablename__ = "workout_steps"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    workout_id: Mapped[str] = mapped_column(String, ForeignKey("workouts.id"), nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String, nullable=False)
    targets: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)

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
