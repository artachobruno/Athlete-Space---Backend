from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""


class StravaAuth(Base):
    """Strava OAuth token storage.

    Stores only the minimal data required to mint future access tokens:
    - athlete_id: Stable Strava user identifier
    - refresh_token: Long-lived credential for token refresh
    - expires_at: UNIX timestamp for access token expiry

    Access tokens are never persisted - they are ephemeral and discarded after use.
    """

    __tablename__ = "strava_auth"

    athlete_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Activity(Base):
    """Activity records from various sources (Strava, Garmin, etc.)."""

    __tablename__ = "activities"

    activity_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float] = mapped_column(Float, nullable=False)
    avg_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
