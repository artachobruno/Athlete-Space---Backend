"""Core immutable data models for planning.

This module defines the canonical data structures that represent:
- Plan context (intent, race distance, duration)
- Macro planning (weekly focus and volume)
- Weekly structure (day patterns)
- Final planned output (sessions and weeks)

All models are frozen (immutable) to ensure data integrity.
"""

from dataclasses import dataclass

from app.planner.enums import (
    DayType,
    PlanType,
    RaceDistance,
    TrainingIntent,
    WeekFocus,
)


# -----------------------------
# Plan Metadata
# -----------------------------
@dataclass(frozen=True)
class PlanContext:
    """Immutable plan context defining user intent and plan parameters.

    Attributes:
        plan_type: Type of plan (race or season)
        intent: User's training intent (maintain, build, explore, recover)
        weeks: Total number of weeks in the plan
        race_distance: Race distance (required for race plans, None for seasons)
        target_date: Target race date in ISO format (optional, race plans only)
        philosophy: Selected training philosophy (set after B2.5)
    """

    plan_type: PlanType
    intent: TrainingIntent
    weeks: int
    race_distance: RaceDistance | None = None  # None for seasons
    target_date: str | None = None  # ISO date, race only
    philosophy: "PhilosophySelection | None" = None  # Set after B2.5


@dataclass(frozen=True)
class PhilosophySelection:
    """Immutable training philosophy selection.

    This represents the selected training philosophy for the entire plan.
    Once selected, all downstream steps (B3-B6) only use structures and templates
    from this philosophy's namespace.

    Attributes:
        philosophy_id: Philosophy identifier (e.g., "daniels", "pfitzinger", "koop")
        domain: Domain type ("running" | "ultra")
        audience: Target audience level ("beginner" | "intermediate" | "advanced")
    """

    philosophy_id: str
    domain: str
    audience: str


@dataclass(frozen=True)
class PlanRuntimeContext:
    """Immutable runtime context for plan execution.

    This combines the original plan context with the selected philosophy.
    This avoids mutating PlanContext and keeps runtime state explicit.

    Attributes:
        plan: Original plan context
        philosophy: Selected training philosophy
    """

    plan: PlanContext
    philosophy: PhilosophySelection


# -----------------------------
# Macro Planning
# -----------------------------
@dataclass(frozen=True)
class MacroWeek:
    """Immutable macro-level week definition.

    Attributes:
        week_index: Week number (1-based)
        focus: Training focus for this week (RAG-mapped)
        total_distance: Total weekly distance (unit-agnostic)
    """

    week_index: int
    focus: WeekFocus
    total_distance: float  # unit-agnostic


# -----------------------------
# Weekly Structure
# -----------------------------
@dataclass(frozen=True)
class DaySkeleton:
    """Immutable day skeleton defining day pattern.

    Attributes:
        day_index: Day index (0 = Monday, 6 = Sunday)
        day_type: Type of training day
    """

    day_index: int  # 0 = Monday
    day_type: DayType


@dataclass(frozen=True)
class WeekStructure:
    """Immutable week structure loaded from RAG.

    This represents a complete week structure specification from a plan_structure
    document, including day patterns, rules, session groups, and guards.

    Attributes:
        structure_id: Unique structure identifier from RAG
        philosophy_id: Associated philosophy ID (e.g., "daniels")
        focus: Training focus for this week
        days: List of day skeletons (one per day of week)
        rules: Rules dictionary (hard_days_max, no_consecutive_hard_days, etc.)
        session_groups: Mapping of group names to lists of session types
        guards: Guards dictionary for conditional constraints
        day_index_to_session_type: Mapping of day_index (0-6) to session_type (e.g., "threshold", "vo2")
    """

    structure_id: str
    philosophy_id: str
    focus: WeekFocus
    days: list[DaySkeleton]
    rules: dict[str, str | int | bool | dict[str, str | int]]
    session_groups: dict[str, list[str]]
    guards: dict[str, str | int]
    day_index_to_session_type: dict[int, str]


@dataclass(frozen=True)
class DistributedDay:
    """Immutable day with allocated distance.

    Attributes:
        day_index: Day index (0 = Monday, 6 = Sunday)
        day_type: Type of training day
        distance: Allocated distance for this day (unit-agnostic)
    """

    day_index: int
    day_type: DayType
    distance: float


# -----------------------------
# Session Templates
# -----------------------------
@dataclass(frozen=True)
class SessionTemplate:
    """Immutable session template definition.

    Attributes:
        template_id: Unique template identifier
        description_key: Key for human-readable description lookup
        kind: Template kind (e.g., "easy_continuous", "cruise_intervals")
        params: Template parameters dictionary
        constraints: Template constraints dictionary
        tags: List of tags for categorization
    """

    template_id: str
    description_key: str
    kind: str
    params: dict[str, str | int | float | list[str | int | float]]
    constraints: dict[str, str | int | float | list[str | int | float]]
    tags: list[str]


@dataclass(frozen=True)
class SessionTemplateSet:
    """Immutable session template set loaded from RAG.

    Attributes:
        domain: Domain type (e.g., "running", "ultra")
        philosophy_id: Philosophy identifier (e.g., "daniels")
        phase: Training phase (e.g., "build", "taper")
        session_type: Session type (e.g., "easy", "threshold", "vo2")
        race_types: List of race types this set applies to
        audience: Target audience level
        priority: Priority for selection (higher = preferred)
        version: Version string
        templates: List of session templates in this set
    """

    domain: str
    philosophy_id: str
    phase: str
    session_type: str
    race_types: list[str]
    audience: str
    priority: int
    version: str
    templates: list[SessionTemplate]


# -----------------------------
# Final Output Objects
# -----------------------------
@dataclass(frozen=True)
class PlannedSession:
    """Immutable planned training session.

    Attributes:
        day_index: Day index (0 = Monday, 6 = Sunday)
        day_type: Type of training day
        distance: Session distance (unit-agnostic)
        template: Selected session template
        text_output: Optional session text output (set by B6)
    """

    day_index: int
    day_type: DayType
    distance: float
    template: SessionTemplate
    text_output: "SessionTextOutput | None" = None

    def with_text(self, text_output: "SessionTextOutput") -> "PlannedSession":
        """Create a new PlannedSession with text output added.

        Args:
            text_output: Session text output

        Returns:
            New PlannedSession with text_output set
        """
        return PlannedSession(
            day_index=self.day_index,
            day_type=self.day_type,
            distance=self.distance,
            template=self.template,
            text_output=text_output,
        )


@dataclass(frozen=True)
class SessionTextInput:
    """Immutable input for session text generation.

    Attributes:
        philosophy_id: Philosophy identifier
        race_distance: Race distance (or None for seasons)
        phase: Training phase (build/taper)
        week_index: Week number (1-based)
        day_type: Type of training day
        allocated_distance_mi: Allocated distance in miles
        allocated_duration_min: Allocated duration in minutes (optional)
        template_id: Template identifier
        template_kind: Template kind
        params: Template parameters
        constraints: Template constraints
    """

    philosophy_id: str
    race_distance: str
    phase: str
    week_index: int
    day_type: DayType
    allocated_distance_mi: float
    allocated_duration_min: float | None
    template_id: str
    template_kind: str
    params: dict[str, str | int | float | list[str | int | float]]
    constraints: dict[str, str | int | float | list[str | int | float]]


@dataclass(frozen=True)
class SessionTextOutput:
    """Immutable output from session text generation.

    Attributes:
        title: Session title
        description: Full session description
        structure: Structured breakdown (warmup_mi, main, cooldown_mi)
        computed: Derived metrics (total_distance_mi, hard_minutes, intensity_minutes)
    """

    title: str
    description: str
    structure: dict[str, float | list[dict[str, str | int | float]]]
    computed: dict[str, float | int | str | dict[str, int]]


@dataclass(frozen=True)
class PlannedWeek:
    """Immutable planned week with all sessions.

    Attributes:
        week_index: Week number (1-based)
        focus: Training focus for this week
        sessions: List of planned sessions for this week
    """

    week_index: int
    focus: WeekFocus
    sessions: list[PlannedSession]
