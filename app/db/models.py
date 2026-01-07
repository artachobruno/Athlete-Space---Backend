from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""


class User(Base):
    """User table for authentication and user context.

    Stores:
    - id: UUID string (from Clerk user ID)
    - email: User email (optional, can be updated later)
    - created_at: Timestamp when user was created
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


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
    """Strava activity records stored as immutable facts.

    Step 4: Historical activity ingestion from Strava.
    Stores activities with full raw JSON for future processing.

    Schema:
    - id: UUID primary key
    - user_id: Foreign key to users.id (Clerk user ID)
    - athlete_id: Strava athlete ID (string, indexed)
    - strava_activity_id: Strava's activity ID (string)
    - source: Activity source (string, default: "strava")
    - start_time: Activity start time in UTC (datetime, indexed)
    - type: Activity type (string, e.g., "Run", "Ride")
    - duration_seconds: Activity duration in seconds (integer)
    - distance_meters: Distance in meters (float)
    - elevation_gain_meters: Elevation gain in meters (float)
    - raw_json: Full raw JSON response from Strava API (JSONB)
    - streams_data: Time-series streams data from Strava (GPS, HR, power, cadence, etc.) (JSONB)
    - created_at: Record creation timestamp

    Constraints:
    - Unique constraint: (user_id, strava_activity_id) prevents duplicates
    - All timestamps are UTC (no timezone ambiguity)
    - raw_json stores complete Strava response for future use
    """

    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    strava_activity_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="strava", default="strava")

    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_meters: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_gain_meters: Mapped[float] = mapped_column(Float, nullable=False)

    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    streams_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "strava_activity_id", name="uq_activity_user_strava_id"),)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # Enforce one Strava account per user
        # Foreign key constraint handled at application level (SQLite compatibility)
    )


class DailyTrainingLoad(Base):
    """Daily training load metrics (CTL, ATL, TSB).

    Step 6: Derived metrics computed from activities.
    Stores daily aggregated training load metrics.

    Schema:
    - user_id: Foreign key to users.id (Clerk user ID)
    - date: Date (YYYY-MM-DD, indexed)
    - ctl: Chronic Training Load (42-day EMA)
    - atl: Acute Training Load (7-day EMA)
    - tsb: Training Stress Balance (CTL - ATL)
    - load_score: Daily training load score (computed from activities)
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

    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_load_user_date"),)


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

    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_weekly_summary_user_week"),)


class SeasonPlan(Base):
    """LLM-generated season plan storage.

    Stores season-level training plans generated by the LLM.
    Each plan is versioned and timestamped for auditability.
    """

    __tablename__ = "season_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Plan data (stored as JSON)
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
    """

    __tablename__ = "weekly_intents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Intent data (stored as JSON)
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
    """

    __tablename__ = "daily_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    athlete_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Decision data (stored as JSON)
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

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("user_id", "date", "time", "title", name="uq_planned_session_user_datetime_title"),)


class AthleteProfile(Base):
    """Athlete profile information for onboarding and coaching.

    Stores athlete profile data from Strava and user input.
    Fields are source-tagged to track where data came from.

    Schema:
    - user_id: Foreign key to users.id (primary key)
    - name: Full name (from Strava or user input)
    - email: Email address (user input only)
    - gender: Gender (M/F/null, from Strava or user input)
    - date_of_birth: Date of birth (user input only)
    - weight_kg: Weight in kilograms (from Strava or user input)
    - height_cm: Height in centimeters (user input only, never from Strava)
    - location: Location string (city, state, country from Strava or user input)
    - age: Age in years (user input only, never from Strava)
    - unit_system: Unit system preference ("imperial" or "metric")
    - primary_goal: Primary training goal (user input only, never from Strava)
    - onboarding_completed: Whether onboarding is complete (default: False)
    - sources: JSON field tracking source of each field ("strava" | "user")
    - strava_athlete_id: Strava athlete ID (integer, nullable)
    - strava_connected: Whether Strava is connected (default: False)
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp
    """

    __tablename__ = "athlete_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)  # M, F, or None
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_system: Mapped[str | None] = mapped_column(String, nullable=True, default="metric")  # "imperial" or "metric"
    primary_goal: Mapped[str | None] = mapped_column(String, nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sources: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    strava_athlete_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    strava_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UserSettings(Base):
    """User settings for training preferences, privacy, and notifications.

    Stores user preferences and settings for the application.
    One settings record per user.

    Schema:
    - user_id: Foreign key to users.id (primary key)
    - Training Preferences:
      - years_of_training: Years of structured training experience
      - primary_sports: JSON array of sports (e.g., ["Running", "Cycling"])
      - available_days: JSON array of available training days (e.g., ["Mon", "Tue"])
      - weekly_hours: Weekly training hours (float, 3-25)
      - training_focus: "race_focused" or "general_fitness"
      - injury_history: Boolean indicating injury history
    - Privacy Settings:
      - profile_visibility: "public", "private", "coaches" (default: "private")
      - share_activity_data: Boolean for anonymized activity data sharing
      - share_training_metrics: Boolean for sharing metrics with coaches
    - Notifications:
      - email_notifications: Boolean (default: True)
      - push_notifications: Boolean (default: True)
      - workout_reminders: Boolean (default: True)
      - training_load_alerts: Boolean (default: True)
      - race_reminders: Boolean (default: True)
      - weekly_summary: Boolean (default: True)
      - goal_achievements: Boolean (default: True)
      - coach_messages: Boolean (default: True)
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp
    """

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)

    # Training Preferences
    years_of_training: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    primary_sports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    available_days: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    weekly_hours: Mapped[float | None] = mapped_column(Float, nullable=True, default=10.0)
    training_focus: Mapped[str | None] = mapped_column(String, nullable=True, default="general_fitness")
    injury_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Privacy Settings
    profile_visibility: Mapped[str] = mapped_column(String, nullable=False, default="private")
    share_activity_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    share_training_metrics: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Notifications
    email_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    workout_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    training_load_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    race_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weekly_summary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    goal_achievements: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    coach_messages: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
