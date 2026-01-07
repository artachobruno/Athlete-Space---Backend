from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""


class User(Base):
    """User table for authentication and user context.

    Unified user model supporting both email/password and OAuth authentication.
    Stores:
    - id: User ID (string UUID format)
    - email: User email (optional, nullable, unique when set)
    - password_hash: Hashed password (optional, nullable)
    - strava_athlete_id: Strava athlete ID (optional, nullable, unique when set)
    - created_at: Timestamp when user was created
    - last_login_at: Timestamp of last login (optional, nullable)
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    strava_athlete_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_users_email", "email", unique=False),  # Already unique, but explicit index
        Index("idx_users_strava_athlete_id", "strava_athlete_id", unique=False),  # Already unique, but explicit index
    )


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
    """Strava activity records stored as immutable facts.

    Step 1: Raw activity data from Strava API.
    Activities are never updated - only inserted.
    Duplicate prevention via unique constraint on (user_id, strava_activity_id).

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id (Clerk user ID)
    - strava_activity_id: Strava's activity ID (for duplicate prevention)
    - athlete_id: Strava athlete ID (for filtering)
    - type: Activity type (run, ride, etc.)
    - start_time: Activity start timestamp (UTC, indexed)
    - duration_seconds: Activity duration
    - distance_meters: Distance in meters
    - elevation_gain_meters: Elevation gain
    - raw_json: Full Strava API response (JSON)
    - source: Source system (default: "strava")
    - created_at: Record creation timestamp

    Constraints:
    - Unique constraint: (user_id, strava_activity_id) prevents duplicates
    - All timestamps are UTC (no timezone ambiguity)
    - Activities are immutable (no updates, only inserts)
    """

    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    strava_activity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    streams_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="strava")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "strava_activity_id", name="uq_activity_user_strava_id"),
        Index("idx_activities_user_start_time", "user_id", "start_time"),  # Common query: user activities by date range
    )


class CoachMessage(Base):
    """Coach chat message history storage."""

    __tablename__ = "coach_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


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

    Stores individual training sessions that are planned for future dates.
    These sessions can be displayed in the calendar and tracked for completion.
    """

    __tablename__ = "planned_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Session details
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    time: Mapped[str | None] = mapped_column(String, nullable=True)  # HH:MM format
    type: Mapped[str] = mapped_column(String, nullable=False)  # Run, Bike, Swim, etc.
    title: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    intensity: Mapped[str | None] = mapped_column(String, nullable=True)  # easy, moderate, hard, race
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Planning context
    plan_type: Mapped[str] = mapped_column(String, nullable=False)  # "race" or "season"
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # Reference to race/season plan
    week_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Week in the plan

    # Status tracking
    status: Mapped[str] = mapped_column(String, nullable=False, default="planned")  # planned, completed, skipped, cancelled

    # Completion tracking
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_activity_id: Mapped[str | None] = mapped_column(String, nullable=True)  # Link to actual Activity if completed

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_planned_sessions_user_date", "user_id", "date"),  # Common query: user sessions by date range
    )


class AthleteProfile(Base):
    """Athlete profile information for onboarding and coaching.

    Stores athlete-specific data that influences coaching decisions.
    """

    __tablename__ = "athlete_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Basic info
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    units: Mapped[str] = mapped_column(String, nullable=False, default="metric")
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weekly_summary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Training preferences
    years_of_training: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_sports: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    available_days: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    weekly_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_focus: Mapped[str | None] = mapped_column(String, nullable=True)
    injury_history: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    injury_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    consistency: Mapped[str | None] = mapped_column(String, nullable=True)
    goal: Mapped[str | None] = mapped_column(String, nullable=True)

    # Privacy settings
    profile_visibility: Mapped[str | None] = mapped_column(String, nullable=True)
    share_activity_data: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    share_training_metrics: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Notification preferences
    push_notifications: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    workout_reminders: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    training_load_alerts: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    race_reminders: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    goal_achievements: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    coach_messages: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
