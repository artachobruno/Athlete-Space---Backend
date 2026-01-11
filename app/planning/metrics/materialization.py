"""Materialization Metrics.

Observability and metrics for Phase 5 session materialization.
Logs materialization outcomes for monitoring and analysis.
"""

from loguru import logger

from app.planning.materialization.models import ConcreteSession


def log_materialization_metrics(
    week_index: int,
    concrete_sessions: list[ConcreteSession],
    llm_text_used: bool,
) -> None:
    """Log materialization metrics.

    Logs:
    - Sessions materialized
    - LLM text used (yes/no)
    - Average distance per session
    - Total duration
    - Template IDs used

    Args:
        week_index: Week index
        concrete_sessions: List of materialized concrete sessions
        llm_text_used: Whether LLM coach text was generated
    """
    if not concrete_sessions:
        logger.debug(
            "Materialization metrics: No sessions materialized",
            week_index=week_index,
        )
        return

    total_duration = sum(s.duration_minutes for s in concrete_sessions)
    total_distance = sum(s.distance_miles for s in concrete_sessions)
    avg_distance = total_distance / len(concrete_sessions) if concrete_sessions else 0.0

    template_ids = [s.session_template_id for s in concrete_sessions]
    template_id_counts: dict[str, int] = {}
    for template_id in template_ids:
        template_id_counts[template_id] = template_id_counts.get(template_id, 0) + 1

    logger.info(
        "Materialization metrics",
        week_index=week_index,
        sessions_materialized=len(concrete_sessions),
        llm_text_used=llm_text_used,
        total_duration_minutes=total_duration,
        total_distance_miles=round(total_distance, 2),
        avg_distance_miles=round(avg_distance, 2),
        template_ids_used=list(set(template_ids)),
        template_id_counts=template_id_counts,
    )
