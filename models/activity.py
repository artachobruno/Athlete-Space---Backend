from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ActivityRecord(BaseModel):
    activity_id: str
    source: Literal["strava", "garmin"]
    sport: str
    start_time: datetime
    duration_sec: int
    distance_m: float
    elevation_m: float
    avg_hr: int | None
    power: dict | None
