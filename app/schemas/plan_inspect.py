"""Plan Inspector schemas for diagnostic/debugging views.

These schemas expose plan intent, phase logic, weekly structure,
coach reasoning, and plan modifications for developer inspection.
"""

from datetime import date as date_type
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PlanModification(BaseModel):
    """A single plan modification with context."""

    type: str = Field(..., description="Modification type (e.g., 'volume_reduction', 'intensity_cap', 'session_swap')")
    affected_session: str | None = Field(None, description="Session that was modified")
    delta: str = Field(..., description="Change description (e.g., '-10%', 'cap at Z2')")
    reason: str = Field(..., description="Human-readable reason for modification")
    trigger: str = Field(..., description="What triggered this modification (e.g., 'fatigue', 'missed_sessions', 'race_change')")


class WeekInspect(BaseModel):
    """Inspection data for a single week."""

    week_index: int = Field(..., description="Week number (1-indexed)")
    date_range: tuple[date_type, date_type] = Field(..., description="Week start and end dates")
    status: Literal["completed", "current", "upcoming"] = Field(..., description="Week status")
    intended_focus: str = Field(..., description="Primary focus for this week")
    planned_key_sessions: list[str] = Field(default_factory=list, description="List of key session descriptions")
    modifications: list[PlanModification] = Field(default_factory=list, description="Modifications applied to this week")


class PlanPhaseInspect(BaseModel):
    """Inspection data for a training phase."""

    name: str = Field(..., description="Phase name (e.g., 'Base', 'Build', 'Peak', 'Taper')")
    intent: str = Field(..., description="Human-readable phase intent")
    weeks: list[WeekInspect] = Field(default_factory=list, description="Weeks in this phase")


class PlanSnapshot(BaseModel):
    """Static plan intent and structure."""

    objective: str = Field(..., description="Plan objective (e.g., 'Marathon Preparation')")
    anchor_type: Literal["race", "objective"] = Field(..., description="What anchors this plan")
    anchor_title: str = Field(..., description="Anchor name (e.g., 'Nashville Marathon' or 'Aerobic Base')")
    anchor_date: date_type | None = Field(None, description="Anchor date if applicable")
    current_phase: str | None = Field(None, description="Current phase name")
    total_weeks: int = Field(..., description="Total weeks in plan")
    weekly_structure: dict[str, str] = Field(
        default_factory=dict,
        description="Weekly structure pattern (e.g., {'Mon': 'Easy', 'Tue': 'Quality'})",
    )


class CoachAssessment(BaseModel):
    """Coach's assessment of the plan."""

    summary: str = Field(..., description="2-4 sentence summary of plan state")
    confidence: Literal["low", "medium", "high"] = Field(..., description="Confidence level")


class PlanChangeLogItem(BaseModel):
    """A single entry in the plan change log."""

    date: date_type = Field(..., description="Date of change")
    change_type: str = Field(..., description="Type of change")
    description: str = Field(..., description="Human-readable description")


class PlanInspectResponse(BaseModel):
    """Complete plan inspection response."""

    plan_snapshot: PlanSnapshot = Field(..., description="Static plan intent")
    phases: list[PlanPhaseInspect] = Field(default_factory=list, description="Phase timeline")
    current_week: WeekInspect | None = Field(None, description="Current week details")
    coach_assessment: CoachAssessment | None = Field(None, description="Coach's assessment")
    change_log: list[PlanChangeLogItem] = Field(default_factory=list, description="Plan change audit trail")
