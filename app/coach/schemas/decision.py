from typing import Literal

from pydantic import BaseModel


class Decision(BaseModel):
    domain: Literal["training", "nutrition", "recovery"]
    priority: int
    recommendation: str
    rationale: str
    confidence: float
