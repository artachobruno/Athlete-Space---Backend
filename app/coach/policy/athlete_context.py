"""Athlete context for Weekly Policy v3.

Pure data object representing athlete characteristics used for adaptive policy decisions.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AthleteContext:
    """Context about the athlete for adaptive policy decisions.

    Attributes:
        experience_level: Athlete's training experience level
        risk_tolerance: Athlete's risk tolerance for training changes
        consistency_score: Historical consistency score (0.0-1.0)
        history_of_injury: Whether athlete has history of injuries
        adherence_reliability: How reliably athlete adheres to plans
    """

    experience_level: Literal["novice", "intermediate", "advanced", "elite"]
    risk_tolerance: Literal["low", "medium", "high"]
    consistency_score: float  # 0.0 - 1.0
    history_of_injury: bool
    adherence_reliability: Literal["low", "medium", "high"]
