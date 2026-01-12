"""Structure specification data models.

This module defines immutable, typed data structures for training plan structures.
All structures are frozen (read-only) to ensure immutability after resolution.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StructureMetadata:
    """Immutable metadata for a structure specification.

    Attributes:
        id: Unique structure identifier
        philosophy_id: Associated philosophy ID
        race_types: List of race types this structure applies to
        audience: Target audience (e.g., "intermediate", "advanced")
        phase: Training phase (e.g., "build", "taper")
        days_to_race_min: Minimum days to race for this structure
        days_to_race_max: Maximum days to race for this structure
        priority: Priority value for resolution (higher = more preferred)
    """

    id: str
    philosophy_id: str
    race_types: list[str]
    audience: str
    phase: str
    days_to_race_min: int
    days_to_race_max: int
    priority: int


@dataclass(frozen=True)
class StructureSpec:
    """Immutable structure specification.

    This represents a complete, validated training structure that defines:
    - Weekly day patterns
    - Session groups (hard, long, easy)
    - Rules and constraints
    - Guards and notes

    Attributes:
        metadata: Structure metadata
        week_pattern: Mapping of day names (mon-sun) to session types
        rules: Rules dictionary (hard_days_max, no_consecutive_hard_days, long_run, etc.)
        session_groups: Mapping of group names to lists of session types
        guards: Optional guards dictionary
        notes: Optional notes dictionary
    """

    metadata: StructureMetadata
    week_pattern: dict[str, str]
    rules: dict[str, str | int | bool | dict[str, str | int]]
    session_groups: dict[str, list[str]]
    guards: dict[str, str | int]
    notes: dict[str, str]
