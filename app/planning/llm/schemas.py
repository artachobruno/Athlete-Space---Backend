"""Phase 4 Selection Schemas.

Input and output schemas for template selection.
All schemas are frozen dataclasses for immutability.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DayTemplateCandidates:
    """Candidate templates for a single day.

    Attributes:
        day: Day of week (mon, tue, wed, thu, fri, sat, sun)
        role: Day role (easy, hard, long, rest)
        duration_minutes: Allocated duration in minutes
        candidate_template_ids: List of valid template IDs to choose from
    """

    day: str
    role: str
    duration_minutes: int
    candidate_template_ids: list[str]


@dataclass(frozen=True)
class WeekSelectionInput:
    """Input for template selection.

    Attributes:
        week_index: Zero-based week index in the plan
        race_type: Race type (5k, 10k, half, marathon, custom)
        phase: Training phase (base, build, peak, taper)
        philosophy_id: Training philosophy identifier
        days: List of day candidates with template options
    """

    week_index: int
    race_type: str
    phase: str
    philosophy_id: str
    days: list[DayTemplateCandidates]


@dataclass(frozen=True)
class WeekTemplateSelection:
    """Template selection output.

    Attributes:
        week_index: Zero-based week index in the plan
        selections: Dictionary mapping day -> session_template_id
    """

    week_index: int
    selections: dict[str, str]
