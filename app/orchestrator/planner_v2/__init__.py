"""B8 — Orchestrator Wiring (Planner v2 Execution).

This module provides the orchestrator that wires B2 → B2.5 → B3 → B4 → B5 → B6 → B7
into a single deterministic orchestration path.

Key guarantees:
- Executes steps in the correct order
- Enforces one tool per turn
- Stops immediately on hard failure
- Emits progress + artifacts for UI and debugging
- Is retry-safe and idempotent

No planning logic lives here. This is control flow only.
"""

from app.orchestrator.planner_v2.errors import (
    LLMSchemaViolationError,
    OrchestratorError,
    PersistenceError,
    StepExecutionError,
    ValidationError,
)
from app.orchestrator.planner_v2.execution import PLANNER_V2_STEPS, run_step
from app.orchestrator.planner_v2.planner_v2_tool import PlannerInput, PlannerResult, planner_v2_tool
from app.orchestrator.planner_v2.progress import emit_plan_summary, emit_planning_progress
from app.orchestrator.planner_v2.state import PlannerV2State

__all__ = [
    "PLANNER_V2_STEPS",
    "LLMSchemaViolationError",
    "OrchestratorError",
    "PersistenceError",
    "PlannerInput",
    "PlannerResult",
    "PlannerV2State",
    "StepExecutionError",
    "ValidationError",
    "emit_plan_summary",
    "emit_planning_progress",
    "planner_v2_tool",
    "run_step",
]
