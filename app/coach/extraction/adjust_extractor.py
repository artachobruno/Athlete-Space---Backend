"""Adjust intent extraction: parse volume delta from user message.

Atomic extraction only. No slot-filling. Executor validates and hard-fails if incomplete.
"""

import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class ExtractedTrainingAdjustment:
    """Extracted training adjustment from user message."""

    adjustment_type: Literal["volume"] = "volume"
    delta_pct: float | None = None  # -0.20 for "reduce 20%", 0.10 for "increase 10%"

    def is_complete(self) -> bool:
        return self.delta_pct is not None


_PERCENT_PATTERNS = [
    (r"(?:reduce|cut|drop|lower|decrease).*?(?:by\s+)?(\d+)\s*%?", -1.0),
    (r"(?:reduce|cut|drop|lower|decrease).*?(?:by\s+)?(\d+)\s+percent", -1.0),
    (r"volume\s+down\s+(\d+)\s*%?", -1.0),
    (r"(?:increase|raise|add|boost).*?(?:by\s+)?(\d+)\s*%?", 1.0),
    (r"(?:increase|raise|add|boost).*?(?:by\s+)?(\d+)\s+percent", 1.0),
    (r"(\d+)\s*%\s*(?:reduction|less|lower)", -1.0),
    (r"(\d+)\s+percent\s*(?:reduction|less|lower)", -1.0),
]


def extract_training_adjustment(message: str) -> ExtractedTrainingAdjustment:
    """Extract volume delta from user message.

    Rules:
    - "reduce volume by 20%" -> delta_pct = -0.20
    - "cut volume 15 percent" -> delta_pct = -0.15
    - "reduce mileage 10" -> delta_pct = -0.10
    - "increase volume by 10%" -> delta_pct = 0.10

    Args:
        message: User message (e.g. "Reduce volume this week by 20%")

    Returns:
        ExtractedTrainingAdjustment; is_complete() True iff delta_pct was parsed.
    """
    if not message or not isinstance(message, str):
        return ExtractedTrainingAdjustment()

    text = message.strip().lower()
    delta_pct: float | None = None

    for pattern, sign in _PERCENT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = int(match.group(1))
            pct = raw / 100.0
            delta_pct = sign * pct
            break

    # "reduce mileage 10" without % — treat as percentage
    if delta_pct is None:
        m = re.search(
            r"(?:reduce|cut|drop|lower|decrease).*?(?:by\s+)?(\d+)\b",
            text,
            re.IGNORECASE,
        )
        if m:
            delta_pct = -1.0 * (int(m.group(1)) / 100.0)

    return ExtractedTrainingAdjustment(adjustment_type="volume", delta_pct=delta_pct)
