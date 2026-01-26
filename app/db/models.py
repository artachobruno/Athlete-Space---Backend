from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship, validates


class Base(DeclarativeBase):
    """Base class for all database models."""


class AuthProvider(enum.Enum):
    """Authentication provider enum."""

    password = "password"  # noqa: S105  # pragma: allowlist secret
    google = "google"


class UserRole(enum.Enum):
    """User role enum."""

    athlete = "athlete"
    coach = "coach"


class User(Base):
    """User table for authentication and user context.

    Users can authenticate via email/password, Google OAuth, or Apple.
    Stores:
    - id: User ID (UUID format)
    - email: User email (required, unique, indexed)
    - auth_provider: Authentication provider ('google', 'email', 'apple')
    - role: User role ('athlete', 'coach', 'admin')
    - timezone: User timezone (default: 'UTC')
    - onboarding_complete: Whether onboarding is complete
    - created_at: Timestamp when user was created
    - updated_at: Last update timestamp
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    auth_provider: Mapped[str] = mapped_column(String, nullable=False)  # CHECK constraint in DB: 'google', 'email', 'apple', 'password'
    google_sub: Mapped[str | None] = mapped_column(String, nullable=True, unique=True, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="athlete")  # CHECK constraint in DB: 'athlete', 'coach', 'admin'
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # CHECK constraint in DB: 'active', 'disabled', 'deleted'
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    athlete: Mapped[Athlete | None] = relationship("Athlete", uselist=False, back_populates="user")
    coach: Mapped[Coach | None] = relationship("Coach", uselist=False, back_populates="user")


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
    backfill_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Sync tracking
    last_successful_sync_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backfill_updated_at: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Error tracking
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Activity(Base):
    """Activity records stored as immutable facts.

    Schema v2: Activities are normalized from raw provider data.
    Activities are never updated - only inserted.
    Duplicate prevention via unique constraint on (user_id, source, source_activity_id).

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id (UUID)
    - source: Source system ('strava', 'manual', 'import')
    - source_activity_id: Provider's activity ID (text, for duplicate prevention)
    - sport: Sport type ('run', 'ride', 'swim', 'strength', 'walk', 'other')
    - starts_at: Activity start timestamp (TIMESTAMPTZ, indexed)
    - ends_at: Activity end timestamp (TIMESTAMPTZ, nullable)
    - duration_seconds: Activity duration (integer, required, >= 0)
    - distance_meters: Distance in meters (nullable, >= 0)
    - elevation_gain_meters: Elevation gain (nullable, >= 0)
    - calories: Calories burned (nullable, >= 0)
    - tss: Training Stress Score (computed, nullable, >= 0)
    - tss_version: Version identifier for TSS computation method (nullable)
    - title: Activity title (nullable)
    - notes: Activity notes (nullable)
    - metrics: JSONB containing HR, pace series, power, laps, raw_json, streams_data, etc.
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp

    Constraints:
    - Unique constraint: (user_id, source, source_activity_id) prevents duplicates
    - All timestamps are TIMESTAMPTZ (timezone-aware)
    - Activities are immutable (no updates, only inserts)
    """

    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="strava")  # CHECK: 'strava', 'manual', 'import'
    source_activity_id: Mapped[str | None] = mapped_column(String, nullable=True)  # Provider's activity ID (text)
    sport: Mapped[str] = mapped_column(String, nullable=False, index=True)  # CHECK: 'run', 'ride', 'swim', 'strength', 'walk', 'other'
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)  # Required, >= 0
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)  # >= 0
    elevation_gain_meters: Mapped[float | None] = mapped_column(Float, nullable=True)  # >= 0
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)  # >= 0
    tss: Mapped[float | None] = mapped_column(Float, nullable=True)  # >= 0
    tss_version: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # JSONB: HR, pace, power, raw_json, streams_data, etc.

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Climate summary columns (coach-facing)
    has_climate_data: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    avg_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_dew_point_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_dew_point_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_avg_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    precip_total_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    heat_stress_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    heat_acclimation_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # v1.1
    effective_heat_stress_index: Mapped[float | None] = mapped_column(Float, nullable=True)  # v1.1
    wind_chill_c: Mapped[float | None] = mapped_column(Float, nullable=True)  # v2.0
    cold_stress_index: Mapped[float | None] = mapped_column(Float, nullable=True)  # v2.0
    cold_tss_adjustment_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # v2.0 optional
    conditions_label: Mapped[str | None] = mapped_column(String, nullable=True)
    heat_tss_adjustment_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    adjusted_tss: Mapped[float | None] = mapped_column(Float, nullable=True)
    climate_model_version: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_activity_id", name="uq_activity_user_source_id"),
        Index("idx_activities_user_time", "user_id", "starts_at"),  # Common query: user activities by date range
    )

    # TEMPORARY: Compatibility properties for migration (schema v2)
    # TODO: Remove these after all code is migrated
    @property
    def start_time(self) -> datetime:
        """Compatibility: map starts_at → start_time (old field name)."""
        return self.starts_at

    @property
    def type(self) -> str:
        """Compatibility: map sport → type (old field name)."""
        return self.sport

    @property
    def strava_activity_id(self) -> str | None:
        """Compatibility: map source_activity_id → strava_activity_id (old field name)."""
        if self.source == "strava":
            return self.source_activity_id
        return None

    @property
    def raw_json(self) -> dict | None:
        """Compatibility: extract raw_json from metrics dict (old field name)."""
        if self.metrics and isinstance(self.metrics, dict):
            return self.metrics.get("raw_json")
        return None

    @property
    def streams_data(self) -> dict | None:
        """Compatibility: extract streams_data from metrics dict (old field name)."""
        if self.metrics and isinstance(self.metrics, dict):
            return self.metrics.get("streams_data")
        return None

    @property
    def athlete_id(self) -> str | None:
        """Compatibility: removed field (schema v2 uses user_id only)."""
        # This always returns None - athlete_id is removed in schema v2
        # Included only to prevent AttributeError during migration
        return None


class CoachMessage(Base):
    """Coach chat message history storage."""

    __tablename__ = "coach_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class CoachProgressEvent(Base):
    """Progress events for coach orchestrator observability.

    Tracks step-by-step progress of coach actions without exposing internal reasoning.
    Each event represents a state transition for a step in an action plan.
    """

    __tablename__ = "coach_progress_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # "planned", "in_progress", "completed", "failed", "skipped"
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class StravaAccount(Base):
    """Strava OAuth account connection per user.

    Stores encrypted OAuth tokens for each user's Strava connection.
    Enforces one Strava account per user via unique constraint.

    Fields:
    - user_id: Foreign key to users.id
    - athlete_id: Strava athlete ID (string)
    - access_token: Encrypted access token (encrypted at rest)
    - refresh_token: Encrypted refresh token (encrypted at rest)
    - expires_at: Token expiration timestamp (Unix epoch seconds)
    - last_sync_at: Last successful sync timestamp (datetime with timezone, nullable)
    - oldest_synced_at: Earliest activity timestamp synced (TIMESTAMPTZ, nullable)
    - full_history_synced: Whether full history backfill is complete (default: False)
    - sync_success_count: Number of successful syncs (for reliability tracking)
    - sync_failure_count: Number of failed syncs (for reliability tracking)
    - last_sync_error: Last sync error message (nullable)
    - created_at: Account creation timestamp
    """

    __tablename__ = "strava_accounts"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    athlete_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)  # Encrypted
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)  # Encrypted
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    oldest_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    full_history_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sync_success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class GoogleAccount(Base):
    """Google OAuth account connection per user.

    Stores encrypted OAuth tokens for each user's Google connection.
    Enforces one Google account per user via unique constraint.

    Fields:
    - user_id: Foreign key to users.id
    - google_sub: Google user ID (sub claim from OAuth token, string)
    - access_token: Encrypted access token (encrypted at rest)
    - refresh_token: Encrypted refresh token (encrypted at rest)
    - expires_at: Token expiration timestamp (TIMESTAMPTZ, timezone-aware datetime)
    - created_at: Account creation timestamp
    """

    __tablename__ = "google_accounts"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    google_sub: Mapped[str] = mapped_column(String, nullable=False, index=True, unique=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)  # Encrypted
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)  # Encrypted
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class DailyTrainingLoad(Base):
    """Daily training load metrics (CTL, ATL, TSB).

    Step 4: Computed metrics derived from activities.
    Stores daily aggregated training load metrics.

    Schema:
    - user_id: Foreign key to users.id (Clerk user ID)
    - day: Date (YYYY-MM-DD, part of composite primary key)
    - ctl: Chronic Training Load
    - atl: Acute Training Load
    - tsb: Training Stress Balance (CTL - ATL)
    - load_model: Load model identifier (default: 'default')
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp

    Constraints:
    - Composite primary key: (user_id, day) prevents duplicates
    - All dates are UTC (no timezone ambiguity)
    - Metrics are recomputable from raw activities
    """

    __tablename__ = "daily_training_load"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False, index=True)

    ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tsb: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_model: Mapped[str] = mapped_column(Text, nullable=False, default="default")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class WeeklyTrainingSummary(Base):
    """Weekly training summary metrics.

    Step 6: Derived metrics computed from activities.
    Stores weekly aggregated training metrics.

    Schema:
    - user_id: Foreign key to users.id (Clerk user ID)
    - week_start: Week start date (Monday, YYYY-MM-DD, indexed)
    - total_duration: Total training duration in seconds
    - total_distance: Total distance in meters
    - total_elevation: Total elevation gain in meters
    - activity_count: Number of activities
    - intensity_distribution: JSON field with zone distribution
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp

    Constraints:
    - Unique constraint: (user_id, week_start) prevents duplicates
    - All dates are UTC (no timezone ambiguity)
    - Metrics are recomputable from raw activities
    """

    __tablename__ = "weekly_training_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    week_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    total_duration: Mapped[int] = mapped_column(Integer, nullable=False)
    total_distance: Mapped[float] = mapped_column(Float, nullable=False)
    total_elevation: Mapped[float] = mapped_column(Float, nullable=False)
    activity_count: Mapped[int] = mapped_column(Integer, nullable=False)

    intensity_distribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_weekly_summary_user_week"),
        Index("idx_weekly_summary_user_week", "user_id", "week_start"),  # Already covered by unique constraint, but explicit for clarity
    )


class SeasonPlan(Base):
    """LLM-generated season plan storage.

    Stores season-level training plans generated by the LLM.
    Each plan is versioned and timestamped for auditability.

    Architecture: Metadata fields for fast queries, payload_json for full data.
    Do NOT query inside payload_json unless absolutely necessary.
    """

    __tablename__ = "season_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Full plan data (stored as JSON - all metadata is in plan_data)
    # Use deferred to handle cases where column doesn't exist yet
    plan_data: Mapped[dict | None] = deferred(mapped_column(JSON, nullable=True))

    # Versioning (optional - may not exist in all database schemas)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class RacePriority(enum.Enum):
    """Race priority enum for multi-race season support."""

    A = "A"  # Primary/main race
    B = "B"  # Secondary race
    C = "C"  # Tune-up/training race


class RacePlan(Base):
    """Race plan storage for multi-race season support.

    Stores individual race information with priority (A/B/C).
    Each athlete can have multiple races in a season, with one active race at a time.

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id
    - athlete_id: Foreign key to athletes.id
    - race_date: Race date (required, indexed)
    - race_distance: Race distance (e.g., "5K", "10K", "Half Marathon", "Marathon", "Ultra")
    - race_name: Optional race name
    - target_time: Optional target finish time (HH:MM:SS format)
    - priority: Race priority (A/B/C, default A)
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp

    Constraints:
    - Unique constraint: (athlete_id, race_date, race_distance) prevents duplicates
    - Priority A should be unique per athlete (enforced in application logic)
    """

    __tablename__ = "race_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Race information
    race_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    race_distance: Mapped[str] = mapped_column(String, nullable=False)
    race_name: Mapped[str | None] = mapped_column(String, nullable=True)
    target_time: Mapped[str | None] = mapped_column(String, nullable=True)  # HH:MM:SS format

    # Priority (A/B/C) - default A for backward compatibility
    priority: Mapped[str] = mapped_column(
        Enum(RacePriority, name="race_priority", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=RacePriority.A.value,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("athlete_id", "race_date", "race_distance", name="uq_race_plan_athlete_date_distance"),
        Index("idx_race_plan_athlete_priority", "athlete_id", "priority"),
    )


class WeeklyIntent(Base):
    """LLM-generated weekly intent storage.

    Stores weekly training intents generated by the LLM.
    Each intent is versioned and linked to a season plan.

    Architecture: Metadata fields for fast queries, payload_json for full data.
    Do NOT query inside payload_json unless absolutely necessary.
    """

    __tablename__ = "weekly_intents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Metadata fields (for fast queries without JSON parsing)
    primary_focus: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g., "base", "build", "peak", "taper"
    total_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_volume_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Full intent data (stored as JSON - fetch only when needed)
    intent_data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Relationships
    season_plan_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Versioning
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Week identification
    week_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("athlete_id", "week_start", "version", name="uq_weekly_intent_athlete_week_version"),)


class DailyDecision(Base):
    """LLM-generated daily decision storage.

    Stores daily training decisions generated by the LLM.
    Each decision is linked to a weekly intent.

    Architecture: Metadata fields for fast queries, payload_json for full data.
    Do NOT query inside payload_json unless absolutely necessary.
    """

    __tablename__ = "daily_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Metadata fields (for fast queries without JSON parsing)
    recommendation_type: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g., "rest", "easy", "moderate", "hard"
    recommended_intensity: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g., "easy", "moderate", "hard"
    has_workout: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Full decision data (stored as JSON - fetch only when needed)
    decision_data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Relationships
    weekly_intent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Versioning
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Day identification
    decision_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("user_id", "decision_date", "version", name="uq_daily_decision_user_date_version"),
        Index("idx_daily_decision_user_date_active", "user_id", "decision_date", postgresql_where=text("is_active IS true")),
    )


class WeeklyReport(Base):
    """LLM-generated weekly coach report storage.

    Stores weekly coach reports generated by the LLM.
    Each report summarizes a completed week.

    Architecture: Metadata fields for fast queries, payload_json for full data.
    Do NOT query inside payload_json unless absolutely necessary.
    """

    __tablename__ = "weekly_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Metadata fields (for fast queries without JSON parsing)
    summary_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # Overall week rating (0-10)
    key_insights_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activities_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adherence_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100

    # Full report data (stored as JSON - fetch only when needed)
    report_data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Versioning
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Week identification
    week_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    week_end: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    __table_args__ = (UniqueConstraint("athlete_id", "week_start", "version", name="uq_weekly_report_athlete_week_version"),)


class PlannedSession(Base):
    """Planned training sessions generated by race/season planning tools.

    Schema v2: Planned sessions for future dates.
    These sessions can be displayed in the calendar and tracked for completion.
    Linking to activities is done via session_links table, not direct foreign keys.

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id (UUID)
    - season_plan_id: Foreign key to season_plans.id (nullable)
    - revision_id: Foreign key to plan_revisions.id (nullable)
    - starts_at: Session start timestamp (TIMESTAMPTZ, required, indexed)
    - ends_at: Session end timestamp (TIMESTAMPTZ, nullable)
    - sport: Sport type ('run', 'ride', 'swim', 'strength', 'other')
    - session_type: Session type ('easy', 'tempo', 'long', 'interval', etc.)
    - title: Session title (nullable)
    - notes: Session notes (nullable)
    - duration_seconds: Planned duration in seconds (nullable, >= 0)
    - distance_meters: Planned distance in meters (nullable, >= 0)
    - intensity: Intensity level ('Z1..Z5', 'RPE', etc., nullable)
    - intent: Workout intent ('rest', 'easy', 'long', 'quality', nullable)
    - workout_id: Foreign key to workouts.id (nullable)
    - status: Status ('planned', 'completed', 'skipped', 'moved', 'cancelled')
    - tags: JSONB array of tags (default: empty array)
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp
    """

    __tablename__ = "planned_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    season_plan_id: Mapped[str | None] = mapped_column(String, ForeignKey("season_plans.id"), nullable=True, index=True)
    revision_id: Mapped[str | None] = mapped_column(String, ForeignKey("plan_revisions.id"), nullable=True, index=True)

    # Canonical time
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Session details
    sport: Mapped[str] = mapped_column(String, nullable=False)  # CHECK: 'run', 'ride', 'swim', 'strength', 'other'
    session_type: Mapped[str | None] = mapped_column(String, nullable=True)  # 'easy', 'tempo', 'long', 'interval', etc.
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_notes: Mapped[str | None] = mapped_column(String(120), nullable=True)  # Max 120 chars, plain text execution guidance
    must_dos: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)  # Unified must-do instructions (JSONB array of strings)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)  # >= 0
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)  # >= 0
    intensity: Mapped[str | None] = mapped_column(String, nullable=True)  # 'Z1..Z5', 'RPE', etc.
    intent: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # 'rest', 'easy', 'long', 'quality', etc.

    # Workout relationship
    workout_id: Mapped[str | None] = mapped_column(String, ForeignKey("workouts.id"), nullable=True, index=True)

    # Status tracking
    # CHECK: 'planned', 'completed', 'skipped', 'moved', 'cancelled'
    status: Mapped[str] = mapped_column(String, nullable=False, default="planned")

    # Tags (JSONB array)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)  # JSONB array of tags

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    # TEMPORARY: Commented out until migration is run
    # The completed_at column doesn't exist in the production database yet.
    # TODO: Uncomment after running migrate_add_planned_session_completion_fields.py
    # Code that accesses completed_at should use getattr(planned, "completed_at", None) for safety

    @property
    def is_locked(self) -> bool:
        """Check if session is locked (has a confirmed session_link).

        Locked sessions cannot be dragged, moved, or deleted.
        In schema v2, we check session_links table instead of completed_activity_id.
        """
        # This will need to be updated to query session_links table
        # For now, return False - the implementation will be added when session_links model is added
        return False

    # TEMPORARY: Compatibility properties for migration (schema v2)
    # TODO: Remove these after all code is migrated
    @property
    def date(self) -> datetime:
        """Compatibility: map starts_at → date (old field name)."""
        return self.starts_at

    @property
    def time(self) -> str | None:
        """Compatibility: extract time string from starts_at (old field name)."""
        if self.starts_at:
            return self.starts_at.strftime("%H:%M")
        return None

    @property
    def type(self) -> str:
        """Compatibility: map sport → type (old field name)."""
        return self.sport

    @property
    def duration_minutes(self) -> int | None:
        """Compatibility: convert duration_seconds → duration_minutes (old field name)."""
        if self.duration_seconds is None:
            return None
        return self.duration_seconds // 60

    @property
    def distance_km(self) -> float | None:
        """Compatibility: convert distance_meters → distance_km (old field name)."""
        if self.distance_meters is None:
            return None
        return self.distance_meters / 1000.0

    @property
    def distance_mi(self) -> float | None:
        """Compatibility: convert distance_meters → distance_mi (old field name)."""
        if self.distance_meters is None:
            return None
        return self.distance_meters / 1609.344

    @property
    def completed_activity_id(self) -> str | None:
        """Compatibility: removed field (schema v2 uses session_links table)."""
        # This always returns None - completed_activity_id is removed in schema v2
        # Included only to prevent AttributeError during migration
        return None

    @property
    def athlete_id(self) -> str | None:
        """Compatibility: removed field (schema v2 uses user_id only)."""
        # This always returns None - athlete_id is removed in schema v2
        # Included only to prevent AttributeError during migration
        return None

    @property
    def plan_id(self) -> str | None:
        """Compatibility: map season_plan_id → plan_id (old field name)."""
        return self.season_plan_id

    __table_args__ = (
        Index("idx_planned_sessions_user_time", "user_id", "starts_at"),  # Common query: user sessions by date range
        Index("idx_planned_sessions_revision", "revision_id"),  # Fast lookup by revision
    )


class CoachFeedback(Base):
    """LLM-generated coach feedback for planned sessions.

    Stores coach insight, instructions, and steps generated by the LLM for today's sessions.
    This enables coach feedback to be persisted and joined into calendar views.

    Schema:
    - id: UUID primary key
    - planned_session_id: Foreign key to planned_sessions.id (required, unique)
    - user_id: Foreign key to users.id (required, indexed)
    - instructions: List of execution instructions (JSON array of strings)
    - steps: Structured workout steps (JSON array of step objects)
    - coach_insight: Coach insight explaining why today matters
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp
    """

    __tablename__ = "coach_feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    planned_session_id: Mapped[str] = mapped_column(String, ForeignKey("planned_sessions.id"), nullable=False, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # LLM-generated content
    instructions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)  # JSON array of instruction strings
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)  # JSON array of step objects
    coach_insight: Mapped[str] = mapped_column(Text, nullable=False)  # Coach insight text

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_coach_feedback_user_created", "user_id", "created_at"),  # Common query: user feedback by date
    )


class SessionLink(Base):
    """Canonical pairing between planned sessions and completed activities.

    Schema v2: Replaces direct foreign keys (planned_session_id, completed_activity_id).
    Enforces one-to-one relationships: one planned_session can link to one activity, vice versa.

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id (UUID)
    - planned_session_id: Foreign key to planned_sessions.id (required, unique)
    - activity_id: Foreign key to activities.id (required, unique)
    - status: Link status ('proposed', 'confirmed', 'rejected')
    - confidence: Confidence score (0.0-1.0, nullable)
    - method: Pairing method ('auto', 'manual')
    - notes: Optional notes (nullable)
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp

    Constraints:
    - Unique on planned_session_id (one link per planned session)
    - Unique on activity_id (one link per activity)
    - Enables many-to-many relationships in the future if needed
    """

    __tablename__ = "session_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    planned_session_id: Mapped[str] = mapped_column(String, ForeignKey("planned_sessions.id"), nullable=False, index=True, unique=True)
    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), nullable=False, index=True, unique=True)

    status: Mapped[str] = mapped_column(String, nullable=False, default="proposed")  # CHECK: 'proposed', 'confirmed', 'rejected'
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0.0-1.0
    method: Mapped[str] = mapped_column(String, nullable=False, default="auto")  # CHECK: 'auto', 'manual'
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_session_links_user", "user_id", "status"),  # Fast lookup by user and status
    )


class PairingDecision(Base):
    """Audit table for pairing decisions between planned sessions and activities.

    Tracks all pairing attempts and decisions for observability and debugging.
    """

    __tablename__ = "pairing_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    planned_session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    activity_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String, nullable=False)  # paired, rejected, manual_unpair
    duration_diff_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class AthleteProfile(Base):
    """Athlete profile information for onboarding and coaching.

    Stores athlete-specific data that influences coaching decisions.
    Schema v2: Matches database schema exactly.
    """

    __tablename__ = "athlete_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)

    # Basic info (schema v2: matches DB columns)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sex: Mapped[str | None] = mapped_column(String, nullable=True)  # CHECK: 'male','female','other'
    gender: Mapped[str | None] = mapped_column(String, nullable=True)  # Alias for sex, used in API
    birthdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_lbs: Mapped[float | None] = mapped_column(Float, nullable=True)  # Imperial weight
    height_in: Mapped[float | None] = mapped_column(Float, nullable=True)  # Imperial height (total inches)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_system: Mapped[str | None] = mapped_column(String, nullable=True)  # 'imperial' or 'metric'
    sources: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Track data source for each field

    # Training metrics (schema v2)
    ftp_watts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    threshold_pace_sec_per_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_weekly_run_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Structured profile data stored as JSONB (for athlete auto profile feature)
    identity: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    goals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    constraints: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    training_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Onboarding status
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserSettings(Base):  # noqa: PLR0904
    """User settings for training preferences, privacy, and notifications.

    Stores user preferences that affect the application behavior.

    Fields:
      - user_id: Foreign key to users.id (primary key)
      - units: Measurement units preference ("metric" or "imperial")
      - timezone: User timezone (IANA timezone string)
      - notifications_enabled: Whether to send notifications
      - email_notifications: Whether to send email notifications
      - weekly_summary: Boolean (default: True)
      - Training preferences
      - Privacy settings
      - Notification preferences
      - created_at: Settings creation timestamp
      - updated_at: Last update timestamp
    """

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Threshold configuration fields (added by migration)
    ftp_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_pace_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # COMPATIBILITY PROPERTIES: Access preferences JSONB fields as direct attributes
    # These allow code to use settings.primary_sports instead of settings.preferences["primary_sports"]

    def _get_pref(self, key: str, default: object = None) -> object:
        """Helper to get a preference value."""
        if self.preferences is None:
            return default
        return self.preferences.get(key, default)

    def _set_pref(self, key: str, value: object) -> None:
        """Helper to set a preference value.

        CRITICAL: Always creates a new dict to trigger SQLAlchemy change detection.
        This ensures JSONB fields are properly saved to the database.
        """
        if self.preferences is None:
            self.preferences = {}
        # Create a new dict to trigger SQLAlchemy change detection
        # This is required for JSONB fields - modifying in place doesn't trigger updates
        new_prefs = dict(self.preferences)
        # Set the value (None is allowed to clear the field)
        new_prefs[key] = value
        self.preferences = new_prefs

    @property
    def primary_sports(self) -> list[str] | None:
        """Primary sports list."""
        result = self._get_pref("primary_sports")
        if result is None:
            return None
        return list(result) if isinstance(result, (list, tuple)) else None

    @primary_sports.setter
    def primary_sports(self, value: list[str] | None) -> None:
        self._set_pref("primary_sports", value)

    @property
    def available_days(self) -> list[str] | None:
        """Available training days."""
        result = self._get_pref("available_days")
        if result is None:
            return None
        return list(result) if isinstance(result, (list, tuple)) else None

    @available_days.setter
    def available_days(self, value: list[str] | None) -> None:
        self._set_pref("available_days", value)

    @property
    def weekly_hours(self) -> float | None:
        """Weekly training hours."""
        result = self._get_pref("weekly_hours")
        if result is None:
            return None
        return float(result) if isinstance(result, (int, float)) else None

    @weekly_hours.setter
    def weekly_hours(self, value: float | None) -> None:
        self._set_pref("weekly_hours", value)

    @property
    def training_focus(self) -> str | None:
        """Training focus (race_focused, general_fitness)."""
        result = self._get_pref("training_focus")
        if result is None:
            return None
        return str(result) if result else None

    @training_focus.setter
    def training_focus(self, value: str | None) -> None:
        self._set_pref("training_focus", value)

    @property
    def injury_history(self) -> bool | None:
        """Whether user has injury history."""
        result = self._get_pref("injury_history")
        if result is None:
            return None
        return bool(result)

    @injury_history.setter
    def injury_history(self, value: bool | None) -> None:
        self._set_pref("injury_history", value)

    @property
    def injury_notes(self) -> str | None:
        """Injury notes."""
        result = self._get_pref("injury_notes")
        if result is None:
            return None
        return str(result) if result else None

    @injury_notes.setter
    def injury_notes(self, value: str | None) -> None:
        self._set_pref("injury_notes", value)

    @property
    def consistency(self) -> str | None:
        """Training consistency/experience level."""
        result = self._get_pref("consistency")
        if result is None:
            return None
        return str(result) if result else None

    @consistency.setter
    def consistency(self, value: str | None) -> None:
        self._set_pref("consistency", value)

    @property
    def units(self) -> str:
        """Measurement units (metric/imperial)."""
        result = self._get_pref("units", "metric")
        return str(result) if result else "metric"

    @units.setter
    def units(self, value: str) -> None:
        self._set_pref("units", value)

    @property
    def timezone(self) -> str:
        """User timezone."""
        result = self._get_pref("timezone", "UTC")
        return str(result) if result else "UTC"

    @timezone.setter
    def timezone(self, value: str) -> None:
        self._set_pref("timezone", value)

    @property
    def notifications_enabled(self) -> bool:
        """Whether notifications are enabled."""
        return bool(self._get_pref("notifications_enabled", True))

    @notifications_enabled.setter
    def notifications_enabled(self, value: bool) -> None:
        self._set_pref("notifications_enabled", value)

    @property
    def email_notifications(self) -> bool:
        """Whether email notifications are enabled."""
        return bool(self._get_pref("email_notifications", False))

    @email_notifications.setter
    def email_notifications(self, value: bool) -> None:
        self._set_pref("email_notifications", value)

    @property
    def weekly_summary(self) -> bool:
        """Whether weekly summary is enabled."""
        return bool(self._get_pref("weekly_summary", True))

    @weekly_summary.setter
    def weekly_summary(self, value: bool) -> None:
        self._set_pref("weekly_summary", value)

    @property
    def profile_visibility(self) -> str:
        """Profile visibility setting."""
        result = self._get_pref("profile_visibility", "private")
        return str(result) if result else "private"

    @profile_visibility.setter
    def profile_visibility(self, value: str) -> None:
        self._set_pref("profile_visibility", value)

    @property
    def share_activity_data(self) -> bool:
        """Whether to share activity data."""
        return bool(self._get_pref("share_activity_data", False))

    @share_activity_data.setter
    def share_activity_data(self, value: bool) -> None:
        self._set_pref("share_activity_data", value)

    @property
    def share_training_metrics(self) -> bool:
        """Whether to share training metrics."""
        return bool(self._get_pref("share_training_metrics", False))

    @share_training_metrics.setter
    def share_training_metrics(self, value: bool) -> None:
        self._set_pref("share_training_metrics", value)

    @property
    def push_notifications(self) -> bool:
        """Whether push notifications are enabled."""
        return bool(self._get_pref("push_notifications", True))

    @push_notifications.setter
    def push_notifications(self, value: bool) -> None:
        self._set_pref("push_notifications", value)

    @property
    def workout_reminders(self) -> bool:
        """Whether workout reminders are enabled."""
        return bool(self._get_pref("workout_reminders", True))

    @workout_reminders.setter
    def workout_reminders(self, value: bool) -> None:
        self._set_pref("workout_reminders", value)

    @property
    def vocabulary_level(self) -> str | None:
        """Coach vocabulary level (foundational, intermediate, advanced).

        Controls the canonical language layer used for workout names,
        narratives, and LLM responses. Defaults to 'intermediate' if not set.
        """
        result = self._get_pref("vocabulary_level")
        return str(result) if result else None

    @vocabulary_level.setter
    def vocabulary_level(self, value: str | None) -> None:
        self._set_pref("vocabulary_level", value)

    @property
    def training_load_alerts(self) -> bool:
        """Whether training load alerts are enabled."""
        return bool(self._get_pref("training_load_alerts", True))

    @training_load_alerts.setter
    def training_load_alerts(self, value: bool) -> None:
        self._set_pref("training_load_alerts", value)

    @property
    def race_reminders(self) -> bool:
        """Whether race reminders are enabled."""
        return bool(self._get_pref("race_reminders", True))

    @race_reminders.setter
    def race_reminders(self, value: bool) -> None:
        self._set_pref("race_reminders", value)

    @property
    def goal_achievements(self) -> bool:
        """Whether goal achievement notifications are enabled."""
        return bool(self._get_pref("goal_achievements", True))

    @goal_achievements.setter
    def goal_achievements(self, value: bool) -> None:
        self._set_pref("goal_achievements", value)

    @property
    def coach_messages(self) -> bool:
        """Whether coach message notifications are enabled."""
        return bool(self._get_pref("coach_messages", True))

    @coach_messages.setter
    def coach_messages(self, value: bool) -> None:
        self._set_pref("coach_messages", value)

    @property
    def years_of_training(self) -> int | None:
        """Years of structured training experience."""
        result = self._get_pref("years_of_training")
        if result is None:
            return None
        return int(result) if isinstance(result, (int, float)) else None

    @years_of_training.setter
    def years_of_training(self, value: int | None) -> None:
        self._set_pref("years_of_training", value)

    @property
    def goal(self) -> str | None:
        """Training goal text."""
        result = self._get_pref("goal")
        if result is None:
            return None
        return str(result) if result else None

    @goal.setter
    def goal(self, value: str | None) -> None:
        self._set_pref("goal", value)


class ConversationOwnership(Base):
    """Conversation ownership mapping.

    Enforces that every conversation_id is owned by exactly one authenticated user.
    This is a hard security boundary - ownership is immutable once established.

    Schema:
    - conversation_id: Conversation ID (primary key, format: c_<UUID>)
    - user_id: Owner user ID (foreign key to users.id)
    - created_at: Ownership creation timestamp

    Constraints:
    - Unique constraint on conversation_id ensures one owner per conversation
    - Ownership never changes after creation (immutable)
    """

    __tablename__ = "conversation_ownership"

    conversation_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_conversation_ownership_user_id", "user_id"),  # Fast lookup by user
    )


class ConversationProgress(Base):
    """Conversation progress state for slot extraction and follow-up resolution.

    Stores stateful slot information per conversation to enable:
    - Cumulative slot accumulation across turns
    - Awaited slot tracking for follow-up questions
    - Context-aware slot resolution
    - Long-term memory via conversation summary (B34)

    Schema:
    - conversation_id: Conversation ID (primary key, format: c_<UUID>)
    - intent: Current intent (e.g., "race_plan", "season_plan")
    - slots: JSON object with slot values (e.g., {"race_distance": "marathon", "race_date": null})
    - awaiting_slots: JSON array of slot names we're waiting for (e.g., ["race_date"])
    - conversation_summary: JSONB structured summary (facts, preferences, goals, open_threads)
    - summary_updated_at: Timestamp when summary was last updated
    - updated_at: Last update timestamp
    """

    __tablename__ = "conversation_progress"

    conversation_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    intent: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    slots: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    awaiting_slots: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    conversation_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Active race pointer - tracks which race is currently in focus for this conversation
    active_race_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_conversation_progress_intent", "intent"),  # Fast lookup by intent
    )


class Conversation(Base):
    """Conversation metadata storage.

    Each conversation has a unique ID and is owned by a user.
    This is the parent table for conversation_messages, conversation_summaries,
    and conversation_progress tables.

    Schema:
    - id: UUID primary key (matches conversation_id in other tables after stripping 'c_' prefix)
    - user_id: Foreign key to users.id
    - title: Optional conversation title
    - status: Conversation status ('active' or 'archived')
    - created_at: Conversation creation timestamp
    - updated_at: Last update timestamp
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # CHECK: 'active', 'archived'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ConversationMessage(Base):
    """Long-term message persistence (B29).

    Append-only storage for every canonical Message in Postgres.
    This table is used for:
    - Debugging and audit trails
    - Analytics and conversation replay
    - Compliance and regulatory requirements
    - Future summarization (B33/B34)

    Postgres is NEVER used for prompts - Redis remains the only short-term working memory.

    Schema:
    - id: UUID primary key (generated on insert)
    - conversation_id: Conversation ID (format: c_<UUID>)
    - user_id: User ID (Clerk user ID)
    - role: Message role - must be one of: user, assistant, system
    - content: Message content (TEXT, not truncated)
    - tokens: Token count (from normalization)
    - ts: ISO-8601 timestamp from Message (UTC)
    - metadata: JSONB metadata dictionary (stored as 'metadata' in DB, accessed as 'message_metadata' in Python)
    - created_at: Record creation timestamp (server-generated)

    Constraints:
    - Append-only (no updates or deletes)
    - Duplicates are acceptable for audit
    - No uniqueness constraints beyond PK
    """

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=True, index=True)  # Temporarily nullable until migration completes
    role: Mapped[str] = mapped_column("sender", String, nullable=False)  # Maps to 'sender' column for backward compatibility
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=True)  # Temporarily nullable until migration completes
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)  # Temporarily nullable until migration completes
    message_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # Note: Constraint on 'sender' column (mapped from 'role') allows 'user','assistant','coach','system'
        # The model uses 'role' but maps to 'sender' column for backward compatibility
        Index("idx_messages_conversation_ts", "conversation_id", "ts"),  # Common query: messages by conversation ordered by time
        Index("idx_messages_user_ts", "user_id", "ts"),  # Common query: messages by user ordered by time
    )

    @validates("conversation_id")
    def _validate_conversation_id(self, _key: str, value: str) -> str:  # noqa: PLR6301
        """Normalize conversation_id by stripping 'c_' prefix if present.

        Database stores conversation_id as UUID type, but the model uses String.
        SQLAlchemy will convert the string to UUID on insert. This validation
        ensures all code paths (including direct model instantiation) normalize
        the conversation_id, making it safe even if repository normalization is skipped.

        Args:
            _key: Field name (always 'conversation_id') - unused but required by SQLAlchemy
            value: Conversation ID value (may have 'c_' prefix)

        Returns:
            UUID string without prefix (e.g., "2423eccd-17be-406b-b48e-0d71399a762a")

        Raises:
            ValueError: If the ID (after stripping prefix) is not a valid UUID
        """
        # Strip 'c_' prefix if present
        if isinstance(value, str) and value.startswith("c_"):
            value = value[2:]

        # Validate it's a valid UUID (SQLAlchemy will convert string to UUID for DB)
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(
                f"Invalid conversation_id format. Expected format: c_<UUID> or <UUID>. "
                f"Received: {value}. Error: {e}"
            ) from e

        return value


class ConversationSummary(Base):
    """Versioned conversation summary storage (B35).

    Append-only storage for conversation summaries with versioning.
    Each summary generation creates a new versioned row. Summaries are never
    updated or deleted, enabling full audit trail and regression-safe memory.

    Postgres is source of truth. Redis is a cache-only optimization.

    Schema:
    - id: UUID primary key
    - conversation_id: Conversation ID (indexed, format: c_<UUID>)
    - version: Monotonically increasing version number per conversation (starts at 1)
    - summary: JSONB structured summary (facts, preferences, goals, open_threads)
    - created_at: Timestamp when summary was created (server-generated, UTC)

    Constraints:
    - Unique constraint: (conversation_id, version) prevents duplicates
    - Index on (conversation_id, version DESC) for O(1) latest retrieval
    - Append-only (no updates or deletes)
    - Versions are monotonically increasing per conversation
    """

    __tablename__ = "conversation_summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "version", name="uq_conversation_summary_version"),
        Index("idx_conversation_summary_latest", "conversation_id", "version"),
    )


class Athlete(Base):
    """First-class Athlete entity that owns data.

    Each user has exactly one athlete. Athletes own their training data
    and are explicitly scoped for multi-coach access in the future.
    """

    __tablename__ = "athletes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, unique=True, index=True)

    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    birthdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped[User] = relationship("User", back_populates="athlete")


class Coach(Base):
    """Coach entity for managing athletes.

    Each user can have one coach record. Coaches are linked to athletes
    via the CoachAthlete join table for explicit access control.
    """

    __tablename__ = "coaches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, unique=True, index=True)

    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user: Mapped[User] = relationship("User", back_populates="coach")


class CoachAthlete(Base):
    """Join table for coach-athlete relationships.

    Explicitly defines which coaches have access to which athletes.
    No implied access - all relationships must be explicitly created.

    Fields:
    - coach_id: Foreign key to coaches.id
    - athlete_id: Foreign key to athletes.id
    - can_edit: Whether the coach can edit athlete data (default: False)
    - created_at: Relationship creation timestamp
    """

    __tablename__ = "coach_athletes"

    coach_id: Mapped[str] = mapped_column(String, ForeignKey("coaches.id"), primary_key=True)
    athlete_id: Mapped[str] = mapped_column(String, ForeignKey("athletes.id"), primary_key=True)

    can_edit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class WorkoutReconciliation(Base):
    """Workout reconciliation results - read-only observation storage.

    Stores HR-based pace reconciliation results comparing planned vs executed workouts.
    This is observation + interpretation only - no plan mutations.

    Fields:
    - planned_session_id: Foreign key to planned_sessions.id
    - effort_mismatch: Classification of effort mismatch (too_easy, on_target, too_hard, unknown)
    - hr_zone: Observed HR zone (if available)
    - recommendation: Human-readable recommendation (if mismatch detected)
    - created_at: Reconciliation creation timestamp
    """

    __tablename__ = "workout_reconciliations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    planned_session_id: Mapped[str] = mapped_column(String, ForeignKey("planned_sessions.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    effort_mismatch: Mapped[str] = mapped_column(String, nullable=False)  # too_easy, on_target, too_hard, unknown
    hr_zone: Mapped[str | None] = mapped_column(String, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("idx_reconciliation_planned_session", "planned_session_id"),
        Index("idx_reconciliation_user_created", "user_id", "created_at"),
    )


class PlanEvaluation(Base):
    """Plan evaluation storage for change decisions.

    Stores evaluations of whether plan changes are needed.
    Enables auditability and prevents re-evaluation drift.

    Schema:
    - id: UUID primary key
    - user_id: User ID
    - athlete_id: Athlete ID
    - plan_version: Plan version identifier (nullable, for future versioning)
    - horizon: Time horizon evaluated (week, season, race)
    - decision: Evaluation decision (no_change, minor_adjustment, modification_required)
    - reasons: JSON array of reason strings
    - recommended_actions: JSON array of recommended actions (nullable)
    - confidence: Confidence score (0.0-1.0)
    - current_state_summary: Text summary of current state
    - created_at: Timestamp when evaluation was created
    """

    __tablename__ = "plan_evaluations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Plan versioning (nullable for now, can link to season_plan_id later)
    plan_version: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Evaluation metadata
    horizon: Mapped[str] = mapped_column(String, nullable=False, index=True)  # week, season, race
    decision: Mapped[str] = mapped_column(String, nullable=False)  # no_change, minor_adjustment, modification_required

    # Evaluation results (stored as JSON)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    recommended_actions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    current_state_summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("idx_plan_evaluation_athlete_horizon_created", "athlete_id", "horizon", "created_at"),
    )


class PlanRevision(Base):
    """Append-only audit table for plan modifications.

    Every plan modification (applied or blocked) is persisted here.
    This enables querying, replaying, and auditing all plan changes.

    Schema:
    - id: UUID primary key
    - user_id: User ID who made the modification
    - athlete_id: Athlete ID whose plan was modified
    - revision_type: Type of revision (modify_day, modify_week, modify_season, modify_race, rollback)
    - status: Status of revision (applied, blocked, pending)
    - reason: Optional reason for modification
    - blocked_reason: Optional reason if blocked
    - affected_start: Start date of affected range (nullable)
    - affected_end: End date of affected range (nullable)
    - deltas: JSON field storing before/after snapshots and changes
    - created_at: Timestamp when revision was created
    - applied: Whether revision was applied (True if status=applied)
    - applied_at: Timestamp when revision was applied (nullable)
    - approved_by_user: Whether user approved this revision (nullable)
    - requires_approval: Whether this revision requires user approval
    - confidence: Confidence score (0.0-1.0) for this revision
    - parent_revision_id: ID of parent revision (for rollbacks)
    """

    __tablename__ = "plan_revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    revision_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # applied | blocked | pending

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    affected_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    affected_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    deltas: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    # New fields for approval and confidence
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by_user: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0.0-1.0

    # For rollbacks
    parent_revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    __table_args__ = (
        Index("idx_plan_revisions_athlete_created", "athlete_id", "created_at"),
    )


class SubjectiveFeedback(Base):
    """Athlete-reported subjective feedback signals.

    Stores fatigue, soreness, motivation, and notes from athlete.
    Used for Phase 2 - Reality Reconciliation.

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id (UUID)
    - date: Date for feedback (Date, indexed)
    - fatigue: Fatigue level (0-10, nullable)
    - soreness: Soreness level (0-10, nullable)
    - motivation: Motivation level (0-10, nullable)
    - note: Optional text note (nullable)
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp
    """

    __tablename__ = "subjective_feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    fatigue: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-10 scale
    soreness: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-10 scale
    motivation: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-10 scale
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_subjective_feedback_user_date"),
        Index("idx_subjective_feedback_user_date", "user_id", "date"),
    )


class DecisionAudit(Base):
    """Decision audit log for coaching recommendations.

    Stores coaching decisions and their inputs/outputs for auditability.
    Append-only log that preserves the reasoning trail.

    Schema:
    - id: Primary key (UUID)
    - user_id: User ID (indexed for fast queries)
    - timestamp: When the decision was made
    - decision_type: Type of decision (e.g., "no_change", "plan_revision", etc.)
    - inputs: JSON dictionary of inputs used to make the decision
    - output: JSON dictionary of decision output/recommendation
    - rationale: Optional JSON dictionary with explanation/rationale

    Design Notes:
    - JSON inputs/outputs preserve flexibility
    - Append-only for auditability
    - Indexed by user_id and timestamp for fast queries
    """

    __tablename__ = "decision_audit"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    decision_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    rationale: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("idx_decision_audit_user_timestamp", "user_id", "timestamp"),
    )


class AthleteBio(Base):
    """Athlete narrative bio.

    Stores the generated narrative bio for athletes along with metadata
    about generation confidence and dependencies.
    """

    __tablename__ = "athlete_bios"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Bio content
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String, nullable=False)  # 'ai_generated', 'user_edited', 'manual'
    depends_on_hash: Mapped[str | None] = mapped_column(String, nullable=True)  # Hash of profile data this bio depends on
    last_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_athlete_bios_user_id", "user_id"),
        Index("idx_athlete_bios_stale", "stale"),
    )


class ActivityClimateSample(Base):
    """Raw climate samples for activities.

    Stores time-series climate data sampled during activity ingestion.
    Each sample represents weather conditions at a specific time/location.
    """

    __tablename__ = "activity_climate_samples"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id", ondelete="CASCADE"), nullable=False, index=True)
    sample_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dew_point_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)

    source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_activity_climate_samples_activity_time", "activity_id", "sample_time"),
    )


class AthleteClimateProfile(Base):
    """Athlete climate baseline for comparison.

    Stores athlete's hometown climate context and baseline metrics.
    Used for comparing activity conditions to athlete's normal environment.
    """

    __tablename__ = "athlete_climate_profile"

    athlete_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    home_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    climate_type: Mapped[str | None] = mapped_column(String, nullable=True)
    avg_summer_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_summer_dew_point_c: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
