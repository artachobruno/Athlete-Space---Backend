from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CoachChatRequest(BaseModel):
    message: str
    days: int = 60
    days_to_race: int | None = None


class CoachChatResponse(BaseModel):
    intent: str
    reply: str
    conversation_id: str | None = None


class ProgressEventResponse(BaseModel):
    """Progress event response model."""

    conversation_id: str
    step_id: str
    label: str
    status: Literal["planned", "in_progress", "completed", "failed", "skipped"]
    timestamp: datetime
    message: str | None = None


class ActionStepResponse(BaseModel):
    """Action step response model."""

    id: str
    label: str


class ProgressResponse(BaseModel):
    """Progress response for a conversation."""

    steps: list[ActionStepResponse]
    events: list[ProgressEventResponse]
