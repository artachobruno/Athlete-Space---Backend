"""Decision bias logic for RAG integration.

This module applies RAG knowledge to bias orchestrator decisions.
RAG can only add preferences and soft constraints - it never changes
required slots, triggers execution, or selects templates.
"""

from app.coach.agents.rag_gate import rag_is_usable
from app.coach.rag.context import RagContext
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse


def apply_rag_bias(
    decision: OrchestratorAgentResponse,
    rag_context: RagContext | None,
) -> OrchestratorAgentResponse:
    """Apply RAG knowledge to bias orchestrator decision.

    RAG can only:
    - Add preferences (soft constraints)
    - Adjust confidence scores
    - Add contextual information

    RAG cannot:
    - Change required slots
    - Trigger execution
    - Select templates
    - Bypass constraints

    Args:
        decision: Orchestrator decision to bias
        rag_context: RAG context (may be None)

    Returns:
        Biased decision (new instance, original unchanged)
    """
    # If RAG is not usable, return decision unchanged
    if not rag_is_usable(rag_context):
        return decision

    # Create a copy to avoid mutating the original
    # Since OrchestratorAgentResponse is a Pydantic model, we can use model_copy
    biased_decision = decision.model_copy(deep=True)

    # Apply biases from RAG chunks
    for chunk in rag_context.chunks:
        # Check for injury risk tags
        if "injury_risk" in chunk.tags:
            # Add conservative progression preference
            # This is a soft constraint - it doesn't change required slots
            if "preferences" not in biased_decision.structured_data:
                biased_decision.structured_data["preferences"] = []
            preferences = biased_decision.structured_data.get("preferences", [])
            if "conservative_progression" not in preferences:
                preferences.append("conservative_progression")
                biased_decision.structured_data["preferences"] = preferences

        # Check for polarized training tags
        if "polarized" in chunk.tags:
            # Add preference to limit threshold volume
            if "preferences" not in biased_decision.structured_data:
                biased_decision.structured_data["preferences"] = []
            preferences = biased_decision.structured_data.get("preferences", [])
            if "limit_threshold_volume" not in preferences:
                preferences.append("limit_threshold_volume")
                biased_decision.structured_data["preferences"] = preferences

        # Check for high volume tags
        if "high_volume" in chunk.tags:
            # Add preference for volume-focused approach
            if "preferences" not in biased_decision.structured_data:
                biased_decision.structured_data["preferences"] = []
            preferences = biased_decision.structured_data.get("preferences", [])
            if "volume_focused" not in preferences:
                preferences.append("volume_focused")
                biased_decision.structured_data["preferences"] = preferences

    # Adjust confidence based on RAG confidence
    # Higher RAG confidence can slightly boost decision confidence
    if rag_context.confidence == "high":
        # Small boost to decision confidence (capped at 1.0)
        biased_decision.confidence = min(1.0, biased_decision.confidence + 0.05)
    elif rag_context.confidence == "medium":
        # Smaller boost
        biased_decision.confidence = min(1.0, biased_decision.confidence + 0.02)

    return biased_decision
