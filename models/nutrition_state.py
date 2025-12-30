from typing import Literal

from pydantic import BaseModel


class NutritionState(BaseModel):
    energy_balance: Literal["deficit", "neutral", "surplus"]
    carb_adequacy: Literal["low", "adequate", "high"]
    protein_adequacy: Literal["low", "adequate"]
    hydration_risk: bool
    supplement_flags: list[str]
