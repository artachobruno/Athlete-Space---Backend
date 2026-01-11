"""Template Selection Metrics.

Observability and metrics for Phase 4 template selection.
Logs selection outcomes for monitoring and analysis.
"""

from loguru import logger

from app.planning.llm.schemas import WeekTemplateSelection


def log_template_selection_metrics(
    selection: WeekTemplateSelection,
    week_index: int,
    philosophy_id: str,
    *,
    used_llm: bool,
    used_fallback: bool,
    rag_used: bool,
    confidence: float | None = None,
) -> None:
    """Log template selection metrics.

    Logs:
    - LLM used (yes/no)
    - Fallback used (yes/no)
    - Confidence (if available)
    - Selected template IDs
    - Philosophy ID
    - RAG used (yes/no)

    Args:
        selection: Template selection output
        week_index: Week index
        philosophy_id: Philosophy identifier
        used_llm: Whether LLM was used
        used_fallback: Whether fallback was used
        rag_used: Whether RAG context was used
        confidence: Optional confidence score
    """
    logger.info(
        "Template selection metrics",
        week_index=week_index,
        philosophy_id=philosophy_id,
        used_llm=used_llm,
        used_fallback=used_fallback,
        rag_used=rag_used,
        confidence=confidence,
        selections_count=len(selection.selections),
        selected_template_ids=list(selection.selections.values()),
    )
