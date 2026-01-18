"""Canonical data interfaces for coach read tools.

This module defines the single source of truth for data structures
returned by read-only coach tools. All tools must use these interfaces.
"""

from datetime import date, datetime

from pydantic import BaseModel


class CompletedActivity(BaseModel):
    """Completed activity from executed workouts."""

    id: str
    sport: str
    start_time: datetime
    duration_min: float
    distance_km: float | None = None
    load: float
    planned_session_id: str | None = None


class PlannedSession(BaseModel):
    """Planned training session."""

    id: str
    date: date
    sport: str
    intensity: str
    target_load: float
    duration_min: float | None = None


class AthleteProfile(BaseModel):
    """Athlete profile information."""

    athlete_id: str
    age: int | None = None
    sex: str | None = None
    training_age_years: int | None = None
    preferred_rest_days: list[str] | None = None
    max_weekly_hours: float | None = None


class CalendarEvent(BaseModel):
    """Calendar event (training or non-training)."""

    id: str
    start_time: datetime
    end_time: datetime
    title: str
    source: str  # "training", "work", "family"


class TrainingMetrics(BaseModel):
    """Training load metrics snapshot."""

    ctl: float
    atl: float
    tsb: float
    weekly_load: float
