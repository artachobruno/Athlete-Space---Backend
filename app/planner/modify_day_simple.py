"""Single-day session modification.

This module provides a simple, embedding-only modifier for replacing one
existing planned session with a better template match given modification intent.

Flow:
1. Existing session + modification context → Query text
2. Embed query
3. Cosine similarity vs ALL templates
4. Pick top-1
5. Replace session fields
6. Done
"""

from loguru import logger

from app.db.models import PlannedSession as DBPlannedSession
from app.domains.training_plan.template_loader import load_all_session_templates
from app.planner.query_builders import build_modify_day_query
from app.planner.selectors.session_selector_embedding_only import select_best_template
from app.planner.session_builder import replace_planned_session


def modify_single_day(
    *,
    existing_session: DBPlannedSession,
    modification_context: dict[str, str],
) -> DBPlannedSession:
    """Modify a single planned session using embedding similarity only.

    This function:
    - Loads ALL session templates for the domain (no filters)
    - Builds query text from existing session + modification context
    - Embeds query and finds best match via cosine similarity
    - Replaces session with new template
    - Returns updated PlannedSession

    Args:
        existing_session: Current planned workout (database model)
        modification_context: Why change is needed (reason, adjustment, etc.)

    Returns:
        Updated PlannedSession with new template

    Raises:
        RuntimeError: If no templates found (configuration error)
        AssertionError: If selector returns no template (should never happen)
    """
    logger.info(
        "Modifying single-day session",
        session_id=existing_session.id,
        template_id=existing_session.template_id,
        reason=modification_context.get("reason", ""),
    )

    # B2: Load ALL session templates (no filters)
    # Extract domain from existing session type or default to "running"
    domain = "running"  # Default domain
    if existing_session.type:
        type_lower = existing_session.type.lower()
        if "run" in type_lower:
            domain = "running"
        elif "bike" in type_lower or "ride" in type_lower:
            domain = "cycling"
        elif "swim" in type_lower:
            domain = "swimming"

    templates_with_embeddings = load_all_session_templates(domain)
    logger.debug(f"Loaded {len(templates_with_embeddings)} templates for domain '{domain}'")

    # B3: Build modification query
    query_text = build_modify_day_query(
        existing_session=existing_session,
        modification_context=modification_context,
    )
    logger.debug(f"Built modification query: {query_text}")

    # B4: Select best template using embedding similarity
    best_template = select_best_template(
        templates_with_embeddings=templates_with_embeddings,
        query_text=query_text,
    )

    # B6: Replace session with new template
    updated_session = replace_planned_session(
        old_session=existing_session,
        new_template=best_template,
        modification_context=modification_context,
    )

    logger.info(
        "Single-day session modified",
        old_template=existing_session.template_id,
        new_template=best_template.template_id,
        reason=modification_context.get("reason", ""),
        session_id=existing_session.id,
    )

    return updated_session
