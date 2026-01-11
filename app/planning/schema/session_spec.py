from enum import StrEnum

from pydantic import BaseModel, Field


class Sport(StrEnum):
    RUN = "run"
    BIKE = "bike"
    SWIM = "swim"


class SessionType(StrEnum):
    EASY = "easy"
    RECOVERY = "recovery"
    LONG = "long"
    TEMPO = "tempo"
    THRESHOLD = "threshold"
    VO2 = "vo2"
    RACE_PACE = "race_pace"
    STRIDES = "strides"


class Intensity(StrEnum):
    VERY_EASY = "very_easy"
    EASY = "easy"
    MODERATE = "moderate"
    TEMPO = "tempo"
    THRESHOLD = "threshold"
    VO2 = "vo2"
    RACE = "race"


class SessionSpec(BaseModel):
    sport: Sport
    session_type: SessionType
    intensity: Intensity

    target_distance_km: float | None = Field(None, gt=0)
    target_duration_min: int | None = Field(None, gt=0)

    goal: str = Field(..., description="Physiological or training goal")
    phase: str = Field(..., description="base | build | peak | taper")

    week_number: int
    day_of_week: int  # 0=Mon ... 6=Sun

    notes: str | None = None

    def validate_volume(self) -> None:
        if not self.target_distance_km and not self.target_duration_min:
            raise ValueError("SessionSpec must include distance or duration")
