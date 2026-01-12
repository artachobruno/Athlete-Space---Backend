"""Session builder for converting templates to planned sessions.

This module converts SessionTemplate objects into PlannedSession objects
for single-day planning.
"""

from datetime import date
from typing import TYPE_CHECKING

from loguru import logger

from app.domains.training_plan.models import SessionTemplate as DomainSessionTemplate
from app.planner.enums import DayType
from app.planner.models import PlannedSession, SessionTemplate

if TYPE_CHECKING:
    from app.db.models import PlannedSession as DBPlannedSession


def build_planned_session(
    template: DomainSessionTemplate,
    session_date: date,
) -> PlannedSession:
    """Build PlannedSession from SessionTemplate.

    Args:
        template: SessionTemplate with embedding
        session_date: Date for the session

    Returns:
        PlannedSession object
    """
    # Extract score if available (from embedding selector)
    score = getattr(template, "_selection_score", None)

    # Create PlannedSession
    # Note: PlannedSession requires day_index, day_type, distance, and template
    # For single-day planning, we use defaults for day_index and day_type
    # Distance will be set later if needed
    # Convert domain SessionTemplate to planner SessionTemplate
    planner_template: SessionTemplate = SessionTemplate(
        template_id=template.template_id,
        description_key=template.description_key,
        kind=template.kind,
        params=template.params,
        constraints=template.constraints,
        tags=template.tags,
    )

    planned_session = PlannedSession(
        day_index=0,  # Default for single-day (not used in week context)
        day_type=DayType.EASY,  # Default (will be inferred from template if needed)
        distance=0.0,  # Default (can be set from user context if available)
        template=planner_template,
        text_output=None,  # Will be generated later if needed
    )

    if score is not None:
        logger.debug(
            "Built planned session from template",
            template_id=template.template_id,
            date=session_date.isoformat(),
            score=score,
        )
    else:
        logger.debug(
            "Built planned session from template",
            template_id=template.template_id,
            date=session_date.isoformat(),
        )

    return planned_session


def replace_planned_session(
    *,
    old_session: "DBPlannedSession",
    new_template: DomainSessionTemplate,
    modification_context: dict[str, str],
) -> "DBPlannedSession":
    """Replace an existing planned session with a new template.

    Preserves:
    - date
    - user_id, athlete_id
    - calendar ID (plan_id)

    Replaces:
    - workout content (title, notes, template_id, session_type)

    Adds metadata:
    - source = "embedding_modify"
    - notes include replacement reason

    Args:
        old_session: Existing PlannedSession (database model)
        new_template: New SessionTemplate to use
        modification_context: Modification context with reason

    Returns:
        Updated PlannedSession (same object, modified in place)
    """
    # Save old template_id before updating
    old_template_id = old_session.template_id

    # Update template_id
    old_session.template_id = new_template.template_id

    # Update session_type from template kind
    # Map template kind to session_type
    kind_lower = new_template.kind.lower()
    if "easy" in kind_lower or "recovery" in kind_lower:
        old_session.session_type = "easy"
    elif "threshold" in kind_lower or "tempo" in kind_lower or "cruise" in kind_lower:
        old_session.session_type = "threshold"
    elif "interval" in kind_lower or "vo2" in kind_lower or "speed" in kind_lower:
        old_session.session_type = "interval"
    elif "long" in kind_lower:
        old_session.session_type = "long"
    else:
        # Use template kind as session_type if no match
        old_session.session_type = new_template.kind

    # Update title based on template kind
    # Capitalize first letter of each word
    title_parts = new_template.kind.split("_")
    title = " ".join(word.capitalize() for word in title_parts)
    old_session.title = title

    # Update notes with template description and modification reason
    reason = modification_context.get("reason", "")
    notes_parts: list[str] = []
    if new_template.description_key:
        notes_parts.append(new_template.description_key)
    if reason:
        notes_parts.append(f"Modified: {reason}")
    if old_session.notes:
        # Preserve existing notes if present
        notes_parts.insert(0, old_session.notes)
    old_session.notes = " | ".join(notes_parts) if notes_parts else None

    # Update source to indicate this was modified via embedding
    old_session.source = "embedding_modify"

    # Store replaced_from in tags if available
    if old_session.tags is None:
        old_session.tags = []
    if old_template_id and f"replaced_from:{old_template_id}" not in old_session.tags:
        # Add old template_id to tags for tracking
        old_session.tags.append(f"replaced_from:{old_template_id}")

    logger.debug(
        "Replaced planned session",
        session_id=old_session.id,
        old_template_id=old_template_id,
        new_template_id=new_template.template_id,
        new_session_type=old_session.session_type,
    )

    return old_session
