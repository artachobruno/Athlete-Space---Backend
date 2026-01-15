"""NLU output types and contracts."""

from typing import Literal

from pydantic import BaseModel


class NLUResult(BaseModel):
    """NLU classification result.

    This represents the output of the NLU classifier, containing
    the intent, horizon, and extracted slots.
    """

    intent: Literal["plan", "modify", "ask", "log", "analyze"]
    horizon: Literal["day", "week", "season", "race"] | None = None
    slots: dict = {}
