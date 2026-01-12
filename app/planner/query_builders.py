"""Query text builders for template selection.

This module builds deterministic query text from user intent for template
selection via embedding similarity.
"""

from app.db.models import PlannedSession


def build_single_day_query(intent_context: dict[str, str]) -> str:
    """Build query text for single-day template selection.

    Args:
        intent_context: Dictionary with keys:
            - domain: Training domain (e.g., "running")
            - session_type: Session type (e.g., "easy", "threshold") (optional)
            - focus: Training focus (optional)

    Returns:
        Query text string ready for embedding
    """
    lines: list[str] = []

    domain = intent_context.get("domain", "running")
    lines.append(f"{domain} training session")

    session_type = intent_context.get("session_type", "")
    if session_type:
        lines.append(session_type)

    focus = intent_context.get("focus", "")
    if focus:
        lines.append(focus)

    lines.append("single day workout")

    return "\n".join(line for line in lines if line)


def build_modify_day_query(
    existing_session: PlannedSession,
    modification_context: dict[str, str],
) -> str:
    """Build query text for single-day session modification.

    Args:
        existing_session: Current planned session (database model)
        modification_context: Dictionary with keys:
            - reason: Why change is needed (e.g., "fatigue adjustment")
            - adjustment: What adjustment (e.g., "reduce intensity")

    Returns:
        Query text string ready for embedding
    """
    lines: list[str] = []

    # Base domain
    lines.append("running training session")

    # Existing session type
    if existing_session.session_type:
        lines.append(existing_session.session_type)
    elif existing_session.type:
        # Fallback to activity type
        type_lower = existing_session.type.lower()
        if "easy" in type_lower or "recovery" in type_lower:
            lines.append("easy")
        elif "threshold" in type_lower or "tempo" in type_lower:
            lines.append("threshold")
        elif "interval" in type_lower or "vo2" in type_lower:
            lines.append("interval")

    # Existing focus (from title or notes)
    if existing_session.title:
        # Extract focus keywords from title
        title_lower = existing_session.title.lower()
        if "long" in title_lower:
            lines.append("long run")
        elif "speed" in title_lower or "fast" in title_lower:
            lines.append("speed work")

    # Modification reason
    reason = modification_context.get("reason", "")
    if reason:
        lines.append(reason)

    # Modification adjustment
    adjustment = modification_context.get("adjustment", "")
    if adjustment:
        lines.append(adjustment)

    # Modification intent markers
    lines.append("replace existing session")
    lines.append("single day modification")

    return "\n".join(line for line in lines if line)
