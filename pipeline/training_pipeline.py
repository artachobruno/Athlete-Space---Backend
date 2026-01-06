from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from app.state.builder import build_training_state
from app.state.models import ActivityRecord, TrainingState

# -------------------------------------------------------------------
# Agent Interface (Contract)
# -------------------------------------------------------------------


class TrainingAgent(Protocol):
    """Decision layer interface.

    Implementations may be rule-based, LLM-based, or hybrid.
    """

    def decide(self, *, state: TrainingState) -> TrainingDecision: ...


# -------------------------------------------------------------------
# Decision Model
# -------------------------------------------------------------------


@dataclass
class TrainingDecision:
    """Lightweight decision output from the pipeline."""

    recommended_intent: str
    explanation: str


# -------------------------------------------------------------------
# Default Stub Agent (Deterministic)
# -------------------------------------------------------------------


class StubTrainingAgent:
    """Safe default agent for testing and early wiring."""

    def decide(self, *, state: TrainingState) -> TrainingDecision:
        _ = self  # Required to match TrainingAgent Protocol interface
        return TrainingDecision(
            recommended_intent=state.recommended_intent,
            explanation="Decision derived from deterministic training state.",
        )


# -------------------------------------------------------------------
# Pipeline
# -------------------------------------------------------------------


def run_training_pipeline(
    *,
    activities: list[ActivityRecord],
    today: dt.date,
    prev_state: TrainingState | None = None,
    agent: TrainingAgent | None = None,
) -> tuple[TrainingState, TrainingDecision]:
    """End-to-end training pipeline.

    Builder → Agent → Decision
    """
    state = build_training_state(
        activities=activities,
        today=today,
        prev_state=prev_state,
    )

    agent = agent or StubTrainingAgent()
    decision = agent.decide(state=state)

    return state, decision
