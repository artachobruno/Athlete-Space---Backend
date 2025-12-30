from pydantic import BaseModel
from typing import Literal

class Decision(BaseModel):
    domain: Literal["training", "nutrition", "recovery"]
    priority: int
    recommendation: str
    rationale: str
    confidence: float
