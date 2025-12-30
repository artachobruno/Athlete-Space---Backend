from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class StravaActivity(BaseModel):
    id: int
    type: str
    start_date: datetime
    elapsed_time: int
    distance: float
    total_elevation_gain: float
    average_heartrate: float | None = None
    average_watts: float | None = None

    raw: dict[str, Any] | None = None
