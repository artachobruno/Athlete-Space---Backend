from pydantic import BaseModel
from typing import List, Literal, Optional

class Task(BaseModel):
    task_id: str
    name: str
    phase: str
    agent_type: Literal[
        "SchemaAgent",
        "AdapterAgent",
        "StateAgent",
        "OrchestratorAgent",
        "PromptAgent",
        "SummaryAgent",
        "Human"
    ]
    inputs: List[str]
    outputs: List[str]
    status: Literal[
        "not_started",
        "in_progress",
        "blocked",
        "review",
        "done"
    ]
    blocking: bool = False
    notes: Optional[str] = None
