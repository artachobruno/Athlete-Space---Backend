"""Domain types for plan regeneration.

Regeneration is a deterministic rebuild of future plan sessions
after a revision boundary, without touching history.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

RegenerationMode = Literal["partial", "full"]


class RegenerationRequest(BaseModel):
    """Request to regenerate a plan from a start date.

    Attributes:
        start_date: Start date for regeneration (must be >= today)
        end_date: Optional end date (if None, regenerates to plan end)
        mode: Regeneration mode ("partial" or "full")
        reason: Optional reason for regeneration
        allow_race_week: Whether to allow regeneration in race week
    """

    start_date: date
    end_date: date | None = None
    mode: RegenerationMode = "partial"
    reason: str | None = None
    allow_race_week: bool = False
