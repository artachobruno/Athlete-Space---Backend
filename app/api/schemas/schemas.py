"""API contract schemas for Phase 1 - Backend Contract & Skeleton.

All Pydantic models defining the API contracts for Dashboard, Calendar, Training, and Coach pages.
These schemas define what the backend promises to return, enabling frontend development
with mock data before implementing real logic.
"""

from pydantic import BaseModel, Field, field_validator

# ============================================================================
# Dashboard Schemas (/me/overview)
# ============================================================================


class DashboardMetrics(BaseModel):
    """Training load metrics for dashboard visualization."""

    ctl: list[tuple[str, float]] = Field(description="List of (date, CTL value) tuples")
    atl: list[tuple[str, float]] = Field(description="List of (date, ATL value) tuples")
    tsb: list[tuple[str, float]] = Field(description="List of (date, TSB value) tuples")


class DashboardToday(BaseModel):
    """Today's training metrics."""

    ctl: float = Field(description="Chronic Training Load")
    atl: float = Field(description="Acute Training Load")
    tsb: float = Field(description="Training Stress Balance")
    tsb_7d_avg: float = Field(description="7-day average TSB")


class DashboardOverviewResponse(BaseModel):
    """Response for GET /me/overview."""

    connected: bool = Field(description="Whether Strava is connected")
    last_sync: str | None = Field(description="ISO 8601 timestamp of last sync", default=None)
    data_quality: str = Field(description="Data quality status: ok | limited | insufficient")
    metrics: DashboardMetrics = Field(description="Training load metrics")
    today: DashboardToday = Field(description="Today's metrics")


# ============================================================================
# Calendar Schemas
# ============================================================================


class CalendarSession(BaseModel):
    """A training session in the calendar."""

    id: str = Field(description="Unique session identifier")
    date: str = Field(description="ISO 8601 date (YYYY-MM-DD)")
    time: str | None = Field(description="Time of day (HH:MM)", default=None)
    type: str = Field(description="Activity type (Run, Bike, Swim, etc.)")
    title: str = Field(description="Session title or name")
    duration_minutes: int | None = Field(description="Planned duration in minutes", default=None)
    distance_km: float | None = Field(description="Planned distance in km", default=None)
    intensity: str | None = Field(description="Intensity level: easy | moderate | hard | race", default=None)
    status: str = Field(description="Session status: planned | completed | skipped | cancelled")
    notes: str | None = Field(description="Optional session notes", default=None)


class CalendarWeekResponse(BaseModel):
    """Response for GET /calendar/week."""

    week_start: str = Field(description="ISO 8601 date of week start (Monday)")
    week_end: str = Field(description="ISO 8601 date of week end (Sunday)")
    sessions: list[CalendarSession] = Field(description="Sessions in this week")


class CalendarSeasonResponse(BaseModel):
    """Response for GET /calendar/season."""

    season_start: str = Field(description="ISO 8601 date of season start")
    season_end: str = Field(description="ISO 8601 date of season end")
    sessions: list[CalendarSession] = Field(description="All sessions in the season")
    total_sessions: int = Field(description="Total number of sessions")
    completed_sessions: int = Field(description="Number of completed sessions")
    planned_sessions: int = Field(description="Number of planned sessions")


class CalendarTodayResponse(BaseModel):
    """Response for GET /calendar/today."""

    date: str = Field(description="ISO 8601 date (YYYY-MM-DD)")
    sessions: list[CalendarSession] = Field(description="Sessions scheduled for today")


class CalendarSessionsResponse(BaseModel):
    """Response for GET /calendar/sessions."""

    sessions: list[CalendarSession] = Field(description="List of sessions")
    total: int = Field(description="Total number of sessions")


# ============================================================================
# Training Schemas
# ============================================================================


class TrainingStateMetrics(BaseModel):
    """Training state metrics (CTL, ATL, TSB)."""

    ctl: float = Field(description="Chronic Training Load")
    atl: float = Field(description="Acute Training Load")
    tsb: float = Field(description="Training Stress Balance")
    trend: str = Field(description="Trend: increasing | stable | decreasing")


class TrainingStateResponse(BaseModel):
    """Response for GET /training/state."""

    current: TrainingStateMetrics = Field(description="Current training state")
    week_volume_hours: float = Field(description="Total training volume this week in hours")
    week_load: float = Field(description="Training load for this week")
    month_volume_hours: float = Field(description="Total training volume this month in hours")
    month_load: float = Field(description="Training load for this month")
    last_updated: str = Field(description="ISO 8601 timestamp of last update")


class TrainingDistributionZone(BaseModel):
    """Training zone distribution data."""

    zone: str = Field(description="Zone name (Zone 1, Zone 2, Zone 3, Zone 4, Zone 5)")
    hours: float = Field(description="Hours in this zone")
    percentage: float = Field(description="Percentage of total volume")


class TrainingDistributionResponse(BaseModel):
    """Response for GET /training/distribution."""

    period: str = Field(description="Period: week | month | season")
    total_hours: float = Field(description="Total training hours in period")
    zones: list[TrainingDistributionZone] = Field(description="Distribution across zones")
    by_type: dict[str, float] = Field(description="Hours by activity type")


class TrainingSignal(BaseModel):
    """A training signal or observation."""

    id: str = Field(description="Unique signal identifier")
    type: str = Field(description="Signal type: fatigue | overreaching | undertraining | readiness")
    severity: str = Field(description="Severity: low | moderate | high")
    message: str = Field(description="Human-readable signal message")
    timestamp: str = Field(description="ISO 8601 timestamp")
    metrics: dict[str, float] = Field(description="Related metrics that triggered this signal")


class TrainingSignalsResponse(BaseModel):
    """Response for GET /training/signals."""

    signals: list[TrainingSignal] = Field(description="List of active training signals")
    summary: str = Field(description="Summary of overall training status")
    recommendation: str | None = Field(description="High-level recommendation", default=None)


# ============================================================================
# Coach Schemas
# ============================================================================


class CoachSummaryResponse(BaseModel):
    """Response for GET /coach/summary."""

    summary: str = Field(description="High-level training summary")
    current_state: str = Field(description="Current training state description")
    next_focus: str = Field(description="Recommended next focus area")
    last_updated: str = Field(description="ISO 8601 timestamp of last update")


class CoachObservation(BaseModel):
    """A coaching observation."""

    id: str = Field(description="Unique observation identifier")
    category: str = Field(description="Category: volume | intensity | recovery | consistency")
    observation: str = Field(description="The observation text")
    timestamp: str = Field(description="ISO 8601 timestamp")
    related_metrics: dict[str, float] = Field(description="Metrics that support this observation")


class CoachObservationsResponse(BaseModel):
    """Response for GET /coach/observations."""

    observations: list[CoachObservation] = Field(description="List of observations")
    total: int = Field(description="Total number of observations")


class CoachRecommendation(BaseModel):
    """A coaching recommendation."""

    id: str = Field(description="Unique recommendation identifier")
    priority: str = Field(description="Priority: high | medium | low")
    category: str = Field(description="Category: volume | intensity | recovery | structure")
    recommendation: str = Field(description="The recommendation text")
    rationale: str = Field(description="Explanation of why this recommendation is made")
    timestamp: str = Field(description="ISO 8601 timestamp")


class CoachRecommendationsResponse(BaseModel):
    """Response for GET /coach/recommendations."""

    recommendations: list[CoachRecommendation] = Field(description="List of recommendations")
    total: int = Field(description="Total number of recommendations")


class CoachConfidenceResponse(BaseModel):
    """Response for GET /coach/confidence."""

    overall: float = Field(description="Overall confidence score (0.0 to 1.0)", ge=0.0, le=1.0)
    data_quality: float = Field(description="Confidence in data quality (0.0 to 1.0)", ge=0.0, le=1.0)
    recommendations: float = Field(description="Confidence in recommendations (0.0 to 1.0)", ge=0.0, le=1.0)
    observations: float = Field(description="Confidence in observations (0.0 to 1.0)", ge=0.0, le=1.0)
    factors: list[str] = Field(description="Factors affecting confidence")
    last_updated: str = Field(description="ISO 8601 timestamp of last update")


class CoachAskRequest(BaseModel):
    """Request for POST /coach/ask."""

    message: str = Field(description="Question or message to the coach")
    context: dict[str, str] | None = Field(description="Optional context data", default=None)


class CoachAskResponse(BaseModel):
    """Response for POST /coach/ask."""

    reply: str = Field(description="Coach's response")
    intent: str | None = Field(description="Detected intent of the message", default=None)
    confidence: float = Field(description="Confidence in the response (0.0 to 1.0)", ge=0.0, le=1.0)
    timestamp: str = Field(description="ISO 8601 timestamp")


# ============================================================================
# Profile Schemas
# ============================================================================


class TargetEvent(BaseModel):
    """Target event information."""

    name: str = Field(description="Event name, e.g., 'Boston Marathon'")
    date: str = Field(description="ISO date, e.g., '2024-04-15'")
    distance: str | None = Field(description="Optional distance, e.g., '26.2 miles'", default=None)


class AthleteProfileResponse(BaseModel):
    """Response for GET /me/profile."""

    full_name: str | None = Field(description="Full name", default=None)
    email: str | None = Field(description="Email address (from auth, read-only)", default=None)
    gender: str | None = Field(description="Gender", default=None)
    date_of_birth: str | None = Field(description="Date of birth (YYYY-MM-DD)", default=None)
    weight_kg: float | None = Field(description="Weight in kilograms", default=None)
    height_cm: int | None = Field(description="Height in centimeters", default=None)
    weight_lbs: float | None = Field(description="Weight in pounds (raw float, no rounding)", default=None)
    height_inches: int | None = Field(description="Height in total inches (integer)", default=None)
    location: str | None = Field(description="Location string", default=None)
    unit_system: str = Field(description="Unit system: imperial or metric", default="imperial")
    strava_connected: bool = Field(description="Whether Strava is connected", default=False)
    target_event: TargetEvent | None = Field(description="User's target race/event information", default=None)
    goals: list[str] = Field(description="Array of user's training goals", default_factory=list)


class AthleteProfileUpdateRequest(BaseModel):
    """Request for PUT /me/profile.

    Full object overwrite - all fields must be provided.
    """

    full_name: str | None = Field(description="Full name", default=None)
    gender: str | None = Field(description="Gender", default=None)
    date_of_birth: str | None = Field(description="Date of birth (YYYY-MM-DD)", default=None)
    weight_kg: float | None = Field(description="Weight in kilograms", default=None)
    height_cm: int | None = Field(description="Height in centimeters", default=None)
    weight_lbs: float | None = Field(description="Weight in pounds (raw float, no rounding)", default=None)
    height_inches: int | None = Field(description="Height in total inches (integer)", default=None)
    location: str | None = Field(description="Location string", default=None)
    unit_system: str | None = Field(description="Unit system: imperial or metric", default=None)
    target_event: TargetEvent | None = Field(description="User's target race/event information", default=None)
    goals: list[str] | None = Field(description="Array of user's training goals (max 5 items, max 200 chars each)", default=None)

    @field_validator("height_cm", mode="before")
    @classmethod
    def convert_height_cm(cls, value: int | float | None) -> int | None:
        """Convert float values to int for height_cm.

        Frontend may send floats (e.g., 180.5) but backend expects integers.
        """
        if value is None:
            return None
        if isinstance(value, float):
            return round(value)
        return value if isinstance(value, int) else None

    @field_validator("height_inches", mode="before")
    @classmethod
    def convert_height_inches(cls, value: int | float | None) -> int | None:
        """Convert to integer for height_inches.

        Args:
            value: Height in total inches

        Returns:
            Integer height in inches or None
        """
        if value is None:
            return None
        if isinstance(value, float):
            return round(value)
        return value if isinstance(value, int) else None


class TrainingPreferencesResponse(BaseModel):
    """Response for GET /me/training-preferences.

    Returns stored values exactly as persisted - no inference of defaults.
    """

    years_of_training: int | None = Field(description="Years of structured training", default=None)
    primary_sports: list[str] | None = Field(description="List of primary sports", default=None)
    available_days: list[str] | None = Field(description="Available training days", default=None)
    weekly_hours: float | None = Field(description="Weekly training hours", default=None)
    training_focus: str | None = Field(description="Training focus: race_focused or general_fitness", default=None)
    injury_history: bool | None = Field(description="Whether athlete has injury history", default=None)
    injury_notes: str | None = Field(
        description="Detailed description of injuries, limitations, or areas of concern",
        default=None,
        max_length=500,
    )
    consistency: str | None = Field(description="User's description of their training consistency level", default=None)
    goal: str | None = Field(description="User's primary training goal in free text", default=None, max_length=200)


class TrainingPreferencesUpdateRequest(BaseModel):
    """Request for PUT /me/training-preferences."""

    years_of_training: int | None = Field(description="Years of structured training", default=None, ge=0)
    primary_sports: list[str] | None = Field(description="List of primary sports", default=None)
    available_days: list[str] | None = Field(description="Available training days", default=None)
    weekly_hours: float | None = Field(description="Weekly training hours", default=None, ge=3.0, le=25.0)
    training_focus: str | None = Field(description="Training focus: race_focused or general_fitness", default=None)
    injury_history: bool | None = Field(description="Whether athlete has injury history", default=None)
    injury_notes: str | None = Field(
        description="Detailed description of injuries, limitations, or areas of concern",
        default=None,
        max_length=500,
    )
    consistency: str | None = Field(description="User's description of their training consistency level", default=None)
    goal: str | None = Field(description="User's primary training goal in free text", default=None, max_length=200)


class PrivacySettingsResponse(BaseModel):
    """Response for GET /me/privacy-settings.

    Returns stored values exactly as persisted - no inference of defaults.
    """

    profile_visibility: str | None = Field(description="Profile visibility: public, private, or coaches", default=None)
    share_activity_data: bool | None = Field(description="Allow sharing anonymized activity data", default=None)
    share_training_metrics: bool | None = Field(description="Allow sharing training metrics with coaches", default=None)


class PrivacySettingsUpdateRequest(BaseModel):
    """Request for PUT /me/privacy-settings."""

    profile_visibility: str | None = Field(description="Profile visibility: public, private, or coaches", default=None)
    share_activity_data: bool | None = Field(description="Allow sharing anonymized activity data", default=None)
    share_training_metrics: bool | None = Field(description="Allow sharing training metrics with coaches", default=None)


class NotificationsResponse(BaseModel):
    """Response for GET /me/notifications.

    Returns stored values exactly as persisted - no inference of defaults.
    """

    email_notifications: bool | None = Field(description="Receive email notifications", default=None)
    push_notifications: bool | None = Field(description="Receive push notifications", default=None)
    workout_reminders: bool | None = Field(description="Receive workout reminders", default=None)
    training_load_alerts: bool | None = Field(description="Receive training load alerts", default=None)
    race_reminders: bool | None = Field(description="Receive race reminders", default=None)
    weekly_summary: bool | None = Field(description="Receive weekly summary", default=None)
    goal_achievements: bool | None = Field(description="Receive goal achievement notifications", default=None)
    coach_messages: bool | None = Field(description="Receive coach message notifications", default=None)


class NotificationsUpdateRequest(BaseModel):
    """Request for PUT /me/notifications."""

    email_notifications: bool | None = Field(description="Receive email notifications", default=None)
    push_notifications: bool | None = Field(description="Receive push notifications", default=None)
    workout_reminders: bool | None = Field(description="Receive workout reminders", default=None)
    training_load_alerts: bool | None = Field(description="Receive training load alerts", default=None)
    race_reminders: bool | None = Field(description="Receive race reminders", default=None)
    weekly_summary: bool | None = Field(description="Receive weekly summary", default=None)
    goal_achievements: bool | None = Field(description="Receive goal achievement notifications", default=None)
    coach_messages: bool | None = Field(description="Receive coach message notifications", default=None)


class ChangePasswordRequest(BaseModel):
    """Request for POST /me/change-password."""

    current_password: str = Field(description="Current password")
    new_password: str = Field(description="New password (min 8 characters)", min_length=8)
    confirm_password: str = Field(description="Confirm new password", min_length=8)


class ChangeEmailRequest(BaseModel):
    """Request for POST /auth/change-email."""

    password: str = Field(description="Current password for verification")
    new_email: str = Field(description="New email address")
