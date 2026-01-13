"""Workout API schemas (Pydantic).

API contract schemas for workout endpoints.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class WorkoutStepSchema(BaseModel):
    """Workout step schema for API responses."""

    id: UUID
    order: int
    type: str
    duration_seconds: int | None
    distance_meters: int | None
    target_metric: str | None
    target_min: float | None
    target_max: float | None
    target_value: float | None
    instructions: str | None
    purpose: str | None
    inferred: bool


class WorkoutStepInputSchema(BaseModel):
    """Workout step input schema for workout creation (no ID)."""

    order: int
    type: str
    duration_seconds: int | None = None
    distance_meters: int | None = None
    target_metric: str | None = None
    target_min: float | None = None
    target_max: float | None = None
    target_value: float | None = None
    instructions: str | None = None
    purpose: str | None = None
    inferred: bool = False


class WorkoutSchema(BaseModel):
    """Workout schema for API responses."""

    id: UUID
    sport: str
    source: str
    total_duration_seconds: int | None
    total_distance_meters: int | None
    steps: list[WorkoutStepSchema]


class WorkoutInputSchema(BaseModel):
    """Workout input schema for workout creation (no ID)."""

    sport: str
    source: str
    total_duration_seconds: int | None = None
    total_distance_meters: int | None = None
    steps: list[WorkoutStepInputSchema]


class TimelineTarget(BaseModel):
    """Target values for a timeline segment."""

    metric: str | None
    min: float | None
    max: float | None
    value: float | None


class WorkoutTimelineSegment(BaseModel):
    """A single segment in the workout timeline."""

    step_id: UUID
    order: int
    step_type: str
    step_color: str
    start_second: int
    end_second: int
    target: TimelineTarget
    purpose: str | None


class WorkoutTimelineResponse(BaseModel):
    """Workout timeline response with time-aligned segments."""

    workout_id: UUID
    total_duration_seconds: int
    segments: list[WorkoutTimelineSegment]


class CreateExportRequest(BaseModel):
    """Request schema for creating a workout export."""

    export_type: Literal["fit"]


class WorkoutExportResponse(BaseModel):
    """Response schema for workout export."""

    id: UUID
    workout_id: UUID
    export_type: str
    status: str
    file_path: str | None
    error_message: str | None
    created_at: str


class AttachActivityRequest(BaseModel):
    """Request schema for attaching an activity to a workout."""

    activity_id: UUID


class StepComplianceSchema(BaseModel):
    """Step compliance schema for API responses."""

    order: int
    compliance_pct: float
    time_in_range_seconds: int
    overshoot_seconds: int
    undershoot_seconds: int
    pause_seconds: int


class WorkoutComplianceResponse(BaseModel):
    """Response schema for workout compliance."""

    overall_compliance_pct: float
    total_pause_seconds: int
    completed: bool
    steps: list[StepComplianceSchema]


class StepInterpretationSchema(BaseModel):
    """Step interpretation schema for API responses."""

    order: int
    rating: str | None
    summary: str | None
    coaching_tip: str | None
    confidence: float | None


class WorkoutInterpretationResponse(BaseModel):
    """Response schema for workout interpretation."""

    verdict: str | None
    summary: str | None
    steps: list[StepInterpretationSchema]


class ParsedStepSchema(BaseModel):
    """Parsed step schema for notes parsing response.

    Represents a single step extracted from workout notes.
    """

    order: int
    type: str
    duration_seconds: int | None = None
    distance_meters: int | None = None


class ParseNotesRequest(BaseModel):
    """Request schema for parsing workout notes into structured steps.

    This endpoint parses free-form workout notes into structured workout steps.
    It does NOT persist any data - it only returns the parsed structure.
    """

    sport: str
    session_type: str | None = None
    notes: str
    total_distance_meters: int | None = None
    total_duration_seconds: int | None = None


class ParseNotesResponse(BaseModel):
    """Response schema for notes parsing.

    Status values:
    - "ok": Steps parsed confidently
    - "unavailable": Feature disabled (safe, expected)
    - "ambiguous": Partial parse; user should reword
    - "failed": Internal error (still non-blocking)

    IMPORTANT: This response does NOT imply any data was persisted.
    Parsing is always non-blocking and non-mutating.
    """

    status: str
    steps: list[ParsedStepSchema] | None = None
    confidence: float = 0.0
    warnings: list[str] = []
