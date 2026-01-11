"""Orchestrator state for turn-based context.

This module defines the orchestrator state that exists only for a single turn.
State is not persisted and not serialized to the client.
"""

from typing import Optional

from pydantic import BaseModel

from app.coach.rag.context import RagContext


class OrchestratorState(BaseModel):
    """Orchestrator state for a single conversation turn.

    This state exists only for the duration of one turn.
    It is not persisted and not serialized to the client.
    """

    rag_context: RagContext | None = None
