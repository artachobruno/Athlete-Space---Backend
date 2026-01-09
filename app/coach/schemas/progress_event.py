"""Progress Event schema for coach orchestrator observability."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ProgressEvent(BaseModel):
    """Progress event for a single step in the action plan."""

    conversation_id: str
    step_id: str
    label: str
    status: Literal["planned", "in_progress", "completed", "failed", "skipped"]
    timestamp: datetime
    message: str | None = None
