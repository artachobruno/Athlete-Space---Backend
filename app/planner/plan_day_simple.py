"""Single-day session planner.

This module provides a simple, embedding-only planner for generating exactly
one training session for one day. No hard filters, no philosophy/phase
enforcement, no fallbacks.

Flow:
1. User intent → Query text
2. Embed query
3. Cosine similarity vs ALL templates
4. Pick top-1
5. Done
"""

from datetime import datetime, timezone

from loguru import logger

from app.domains.training_plan.template_loader import load_all_session_templates
from app.planner.models import PlannedSession
from app.planner.query_builders import build_single_day_query
from app.planner.selectors.session_selector_embedding_only import select_best_template
from app.planner.session_builder import build_planned_session


def plan_single_day(
    *,
    domain: str,
    _user_context: dict[str, str | int | float | None],
    intent_context: dict[str, str],
) -> PlannedSession:
    """Plan a single training session for one day using embedding similarity only.

    This function:
    - Loads ALL session templates for the domain (no filters)
    - Builds query text from intent context
    - Embeds query and finds best match via cosine similarity
    - Returns exactly one PlannedSession

    Args:
        domain: Training domain (e.g., "running")
        user_context: User context (fitness, recent fatigue, etc.) - optional
        intent_context: What the user wants today (session_type, focus, etc.)

    Returns:
        PlannedSession with selected template

    Raises:
        RuntimeError: If no templates found (configuration error)
        AssertionError: If selector returns no template (should never happen)
    """
    logger.info(
        "Planning single-day session",
        domain=domain,
        intent_keys=list(intent_context.keys()),
    )

    # B2: Load ALL session templates (no filters)
    templates_with_embeddings = load_all_session_templates(domain)
    logger.debug(f"Loaded {len(templates_with_embeddings)} templates for domain '{domain}'")

    # B3: Build query text
    query_text = build_single_day_query(intent_context)
    logger.debug(f"Built query text: {query_text}")

    # B4: Select best template using embedding similarity
    best_template = select_best_template(
        templates_with_embeddings=templates_with_embeddings,
        query_text=query_text,
    )

    # B6: Convert template to PlannedSession
    # Use today's date as default (can be overridden by caller)
    session_date = datetime.now(tz=timezone.utc).date()
    planned_session = build_planned_session(
        template=best_template,
        session_date=session_date,
    )

    logger.info(
        "Single-day session planned",
        template_id=best_template.template_id,
        domain=domain,
    )

    return planned_session
