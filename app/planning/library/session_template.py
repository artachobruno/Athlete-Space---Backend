"""SessionTemplate - Library Unit.

Templates describe WHAT a session is, not how long it is.
This is selection-only - no math, no reps, no pacing.
Templates do not contain distance - only time-based constraints.
"""

from dataclasses import dataclass
from typing import Literal

SessionType = Literal[
    "easy",
    "long",
    "tempo",
    "interval",
    "hills",
    "strides",
    "recovery",
    "rest",
]


@dataclass(frozen=True)
class SessionTemplate:
    """Template describing a session type - selection-only.

    This template describes what a session IS, not how long it should be.
    No math, no reps, no pacing - purely descriptive for selection.

    Templates do NOT contain distance - only time-based constraints.

    Attributes:
        id: Unique template identifier
        name: Human-readable template name
        session_type: Type of session
        intensity_level: Intensity level of the session
        race_types: Race types this template applies to
        phase_tags: Phase tags (base, build, peak, taper)
        min_duration_min: Minimum duration in minutes
        max_duration_min: Maximum duration in minutes
        warmup_min: Optional warmup duration (None if not applicable)
        cooldown_min: Optional cooldown duration (None if not applicable)
        structure: Optional structure dict (interpreted later, not computed)
        tags: List of tags for RAG retrieval
    """

    id: str
    name: str

    session_type: SessionType
    intensity_level: Literal["easy", "moderate", "hard"]

    # ---- Applicability ----
    race_types: list[str]
    phase_tags: list[str]  # base, build, peak, taper

    # ---- Time-based constraints (PRIMARY) ----
    min_duration_min: int
    max_duration_min: int

    # ---- Tags (required) ----
    tags: list[str]

    # ---- Structure (optional, interpreted later) ----
    warmup_min: int | None = None
    cooldown_min: int | None = None
    structure: dict[str, str | int | float] | None = None
