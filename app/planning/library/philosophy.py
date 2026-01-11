"""TrainingPhilosophy - Constraints, Not Prose.

This controls WHAT is allowed, not what is written.
Training philosophy defines constraints and parameters for planning.
"""

from dataclasses import dataclass
from typing import Literal

IntensityLevel = Literal["easy", "moderate", "hard"]


@dataclass(frozen=True)
class TrainingPhilosophy:
    """Training philosophy defining planning constraints.

    This controls what is allowed, not what is written.
    Philosophy defines constraints and parameters for planning.

    Attributes:
        id: Unique philosophy identifier
        name: Human-readable philosophy name
        applicable_race_types: Race types this philosophy applies to
        max_hard_days_per_week: Maximum hard days allowed per week
        require_long_run: Whether a long run is required each week
        long_run_ratio_min: Minimum ratio of weekly time for long run (0.0-1.0)
        long_run_ratio_max: Maximum ratio of weekly time for long run (0.0-1.0)
        taper_weeks: Number of taper weeks before race
        taper_volume_reduction_pct: Volume reduction percentage during taper (0.0-100.0)
        preferred_session_tags: Tags to prefer during RAG retrieval (tag -> weight)
    """

    id: str
    name: str

    applicable_race_types: list[str]

    # ---- Structural rules ----
    max_hard_days_per_week: int
    require_long_run: bool

    # ---- Time ratios (PRIMARY) ----
    long_run_ratio_min: float  # 0.0-1.0
    long_run_ratio_max: float  # 0.0-1.0

    # ---- Phase behavior ----
    taper_weeks: int
    taper_volume_reduction_pct: float  # 0.0-100.0

    # ---- Retrieval preferences (used later by RAG) ----
    preferred_session_tags: dict[str, float]  # tag -> weight
