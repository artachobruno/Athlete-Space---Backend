from pydantic import BaseModel, Field


class SessionBlock(BaseModel):
    type: str = Field(..., description="warmup | interval | steady | float | cooldown")
    distance_km: float | None = Field(None, description="Distance in kilometers")
    duration_min: int | None = Field(None, description="Duration in minutes")
    intensity: str = Field(..., description="Intensity level")
    reps: int | None = Field(None, description="Number of repetitions for intervals")
    float_km: float | None = Field(None, description="Recovery distance between intervals")


class SessionPlan(BaseModel):
    title: str = Field(..., description="Session title")
    structure: list[SessionBlock] = Field(..., description="Session structure blocks")
    notes: str | None = Field(None, description="Session notes")
