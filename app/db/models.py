from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    auth_provider: Mapped[str] = mapped_column(String, nullable=False)  # CHECK constraint in DB: 'google', 'email', 'apple'
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
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
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

    # Workout relationship (mandatory invariant)
    workout_id: Mapped[str | None] = mapped_column(String, ForeignKey("workouts.id"), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

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
    - last_sync_at: Last successful sync timestamp (nullable)
    - oldest_synced_at: Earliest activity timestamp synced (Unix epoch seconds, nullable)
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
    last_sync_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oldest_synced_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    - date: Date (YYYY-MM-DD, indexed)
    - ctl: Chronic Training Load
    - atl: Acute Training Load
    - tsb: Training Stress Balance (CTL - ATL)
    - load_score: Daily training load score
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp

    Constraints:
    - Unique constraint: (user_id, date) prevents duplicates
    - All dates are UTC (no timezone ambiguity)
    - Metrics are recomputable from raw activities
    """

    __tablename__ = "daily_training_load"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    ctl: Mapped[float] = mapped_column(Float, nullable=False)
    atl: Mapped[float] = mapped_column(Float, nullable=False)
    tsb: Mapped[float] = mapped_column(Float, nullable=False)
    load_score: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_daily_load_user_date"),
        Index("idx_daily_load_user_date", "user_id", "date"),  # Already covered by unique constraint, but explicit for clarity
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
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Metadata fields (for fast queries without JSON parsing)
    plan_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    primary_race_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    primary_race_name: Mapped[str | None] = mapped_column(String, nullable=True)
    total_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Full plan data (stored as JSON - fetch only when needed)
    plan_data: Mapped[dict] = mapped_column(JSON, nullable=False)

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
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

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

    __table_args__ = (UniqueConstraint("athlete_id", "decision_date", "version", name="uq_daily_decision_athlete_date_version"),)


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
    - status: Status ('planned', 'completed', 'skipped', 'moved', 'canceled')
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
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)  # >= 0
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)  # >= 0
    intensity: Mapped[str | None] = mapped_column(String, nullable=True)  # 'Z1..Z5', 'RPE', etc.
    intent: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # 'rest', 'easy', 'long', 'quality', etc.

    # Workout relationship
    workout_id: Mapped[str | None] = mapped_column(String, ForeignKey("workouts.id"), nullable=True, index=True)

    # Status tracking
    status: Mapped[str] = mapped_column(String, nullable=False, default="planned")  # CHECK: 'planned', 'completed', 'skipped', 'moved', 'canceled'

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
    """

    __tablename__ = "athlete_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)

    # Basic info
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_lbs: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_system: Mapped[str | None] = mapped_column(String, nullable=True)
    strava_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    strava_athlete_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Training history
    years_training: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_sport: Mapped[str | None] = mapped_column(String, nullable=True)

    # Goals
    primary_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_races: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    goals: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    target_event: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extracted_race_attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extracted_injury_attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Race and taper
    race_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    taper_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Health and constraints
    injury_history: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    current_injuries: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    training_constraints: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserSettings(Base):
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

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_conversation_progress_intent", "intent"),  # Fast lookup by intent
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
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    message_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="check_role_valid"),
        Index("idx_messages_conversation_ts", "conversation_id", "ts"),  # Common query: messages by conversation ordered by time
        Index("idx_messages_user_ts", "user_id", "ts"),  # Common query: messages by user ordered by time
    )


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
