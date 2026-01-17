"""Workout execution and compliance database models.

Models for tracking workout execution (activity attachment) and
computing compliance metrics between planned workouts and executed activities.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class MatchType(enum.Enum):
    """Match type for workout executions - planner-level semantics."""

    UNMATCHED = "unmatched"
    AUTO = "auto"
    MANUAL = "manual"


class WorkoutExecution(Base):
    """Workout execution table - links workouts to executed activities.

    Tracks which activity was executed for a given workout plan.
    Stores execution-specific data (duration, distance, status, etc.).
    One workout can have multiple executions (if user repeats the workout).

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id (required, NOT NULL)
    - workout_id: Foreign key to workouts.id
    - activity_id: Foreign key to activities.id
    - planned_session_id: Foreign key to planned_sessions.id (if matched to planned session)
    - duration_seconds: Actual duration of execution (nullable)
    - distance_meters: Actual distance of execution (nullable)
    - status: Execution status (completed, partial, aborted, failed) - execution outcome only
    - match_type: Match type (unmatched, auto, manual) - planner-level semantics
    - created_at: Record creation timestamp
    """

    __tablename__ = "workout_executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    workout_id: Mapped[str] = mapped_column(String, ForeignKey("workouts.id"), nullable=False, index=True)
    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), nullable=False, index=True)
    planned_session_id: Mapped[str | None] = mapped_column(String, ForeignKey("planned_sessions.id"), nullable=True, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_meters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="completed")
    match_type: Mapped[str] = mapped_column(
        SQLEnum(MatchType, name="workout_match_type", create_constraint=True),
        nullable=False,
        default=MatchType.UNMATCHED.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class StepCompliance(Base):
    """Step compliance table - compliance metrics per workout step.

    Stores deterministic compliance metrics for each step in a workout execution.
    Metrics are computed using time-aligned matching between planned steps and activity samples.

    Schema:
    - id: UUID primary key
    - workout_step_id: Foreign key to workout_steps.id
    - duration_seconds: Total duration of the step window
    - time_in_range_seconds: Time spent within target range
    - overshoot_seconds: Time spent above target range
    - undershoot_seconds: Time spent below target range
    - pause_seconds: Time spent paused (cadence=0 or speed<epsilon)
    - compliance_pct: Compliance percentage (time_in_range / (duration - pause))
    """

    __tablename__ = "step_compliance"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    workout_step_id: Mapped[str] = mapped_column(String, ForeignKey("workout_steps.id"), nullable=False, index=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    time_in_range_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    overshoot_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    undershoot_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    pause_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    compliance_pct: Mapped[float] = mapped_column(Float, nullable=False)
    llm_rating: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_tip: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


class WorkoutComplianceSummary(Base):
    """Workout compliance summary table - overall workout compliance metrics.

    Stores aggregated compliance metrics for the entire workout execution.
    One summary per workout execution.

    Schema:
    - workout_id: UUID primary key (one summary per workout)
    - overall_compliance_pct: Weighted average compliance across all steps
    - total_pause_seconds: Total pause time across all steps
    - completed: Whether workout was completed (≥80% steps have non-zero duration)
    """

    __tablename__ = "workout_compliance_summary"

    workout_id: Mapped[str] = mapped_column(String, ForeignKey("workouts.id"), primary_key=True, index=True)
    overall_compliance_pct: Mapped[float] = mapped_column(Float, nullable=False)
    total_pause_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_verdict: Mapped[str | None] = mapped_column(String, nullable=True)
