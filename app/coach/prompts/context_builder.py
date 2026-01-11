"""Prompt context builder for orchestrator.

This module builds context for orchestrator prompts, including RAG summaries.
RAG context is read-only - LLM may read but may not cite non-retrieved info.
"""


from app.coach.agents.orchestrator_state import OrchestratorState
from app.coach.agents.rag_gate import rag_is_usable


def build_prompt_context(state: OrchestratorState) -> list[dict[str, str | list[str]]]:
    """Build prompt context from orchestrator state.

    This function extracts RAG summaries and makes them available
    to the LLM prompt in a read-only format.

    Args:
        state: Orchestrator state with optional RAG context

    Returns:
        List of context dictionaries to include in prompt
    """
    context: list[dict[str, str | list[str]]] = []

    # Add RAG context if available and usable
    if state.rag_context and rag_is_usable(state.rag_context):
        summaries = [chunk.summary for chunk in state.rag_context.chunks]

        context.append({
            "type": "training_knowledge",
            "confidence": state.rag_context.confidence,
            "summaries": summaries,
        })

    return context
