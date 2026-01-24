"""Intent context for Weekly Policy v2.

Pure data object representing the source and strength of a plan change request.
Used by Policy v2 to gate plan changes based on intent characteristics.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IntentContext:
    """Context about the intent behind a plan change request.

    Attributes:
        request_source: Source of the request
            - "athlete_explicit": Direct athlete request (e.g., "I want to train more")
            - "athlete_reflective": Athlete reflection/question (e.g., "Should I adjust?")
            - "system_detected": System-detected issue (e.g., compliance drop)
            - "coach_suggested": Coach-initiated suggestion
        intent_strength: Strength of the intent
            - "weak": Exploratory, uncertain
            - "moderate": Some conviction
            - "strong": Clear, decisive intent
        execution_requested: Whether execution was explicitly requested
    """

    request_source: Literal[
        "athlete_explicit",
        "athlete_reflective",
        "system_detected",
        "coach_suggested",
    ]

    intent_strength: Literal[
        "weak",
        "moderate",
        "strong",
    ]

    execution_requested: bool
