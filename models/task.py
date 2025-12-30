from typing import Literal

from pydantic import BaseModel


class Task(BaseModel):
    task_id: str
    name: str
    phase: str
    agent_type: Literal["SchemaAgent", "AdapterAgent", "StateAgent", "OrchestratorAgent", "PromptAgent", "SummaryAgent", "Human"]
    inputs: list[str]
    outputs: list[str]
    status: Literal["not_started", "in_progress", "blocked", "review", "done"]
    blocking: bool = False
    notes: str | None = None
