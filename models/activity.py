from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime

class ActivityRecord(BaseModel):
    activity_id: str
    source: Literal["strava", "garmin"]
    sport: str
    start_time: datetime
    duration_sec: int
    distance_m: float
    elevation_m: float
    avg_hr: Optional[int] = None
    power: Optional[dict] = None
