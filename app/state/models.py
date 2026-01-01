from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""


class StravaAuth(Base):
    """Strava OAuth token storage and ingestion state.

    Stores:
    - OAuth tokens: athlete_id, refresh_token, expires_at
    - Ingestion state: last_ingested_at, backfill_page, backfill_done
    - Sync tracking: last_successful_sync_at, backfill_updated_at
    - Error tracking: last_error, last_error_at

    Access tokens are ephemeral and obtained via token refresh, not stored.
    """

    __tablename__ = "strava_auth"

    athlete_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ingestion state
    last_ingested_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backfill_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backfill_done: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Sync tracking
    last_successful_sync_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backfill_updated_at: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Error tracking
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Activity(Base):
    """Canonical activity records from various sources (Strava, Garmin, etc.).

    This is the FROZEN canonical schema. All activity data from any source
    (Strava, Garmin, etc.) must map to these fields. UI never sees raw
    source-specific fields.

    Canonical Fields (FROZEN):
    - id: Auto-incrementing primary key (internal use only)
    - athlete_id: Athlete/user ID (integer, indexed for multi-user support)
    - activity_id: Unique identifier from source (e.g., "strava-12345")
    - source: Source system identifier (e.g., "strava", "garmin")
    - start_time: Activity start time in UTC (datetime, indexed)
    - duration_s: Activity duration in seconds (integer)
    - distance_m: Distance in meters (float)
    - elevation_m: Elevation gain in meters (float)
    - sport: Sport type (string, e.g., "run", "ride", "swim")
    - avg_hr: Average heart rate in BPM (optional integer)

    Constraints:
    - All timestamps are UTC (no timezone ambiguity)
    - UI never receives raw Strava/Garmin fields
    - Future sources (Garmin) must map cleanly to this schema
    - Unique constraint: (athlete_id, source, activity_id) prevents duplicates per athlete
    """

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    activity_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)

    sport: Mapped[str] = mapped_column(String, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float] = mapped_column(Float, nullable=False)
    avg_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint("athlete_id", "source", "activity_id", name="uq_activity_athlete_source_id"),)


class CoachMessage(Base):
    """Coach chat message history storage.

    Stores conversation history between athletes and the AI coach.
    """

    __tablename__ = "coach_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
