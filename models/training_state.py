from pydantic import BaseModel
from typing import Literal, Dict

class TrainingState(BaseModel):
    acute_load: float
    chronic_load: float
    load_trend: Literal["rising", "stable", "falling"]
    monotony: float
    recovery_status: Literal["under", "adequate", "over"]
    injury_risk_flag: bool
    intensity_distribution: Dict[str, float]
