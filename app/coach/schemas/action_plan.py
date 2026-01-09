"""Action Plan schema for coach orchestrator observability."""

from pydantic import BaseModel


class ActionStep(BaseModel):
    """A single step in the action plan."""

    id: str
    label: str


class ActionPlan(BaseModel):
    """Structured action plan emitted by orchestrator before tool execution."""

    steps: list[ActionStep]
