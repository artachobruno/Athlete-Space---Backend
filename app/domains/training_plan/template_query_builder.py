"""Query text construction for template selection.

This module builds deterministic query text from planning context for template
selection via embedding similarity.
"""


def build_template_query(
    *,
    domain: str,
    session_type: str,
    race_distance: str | None = None,
    phase: str | None = None,
    philosophy: str | None = None,
) -> str:
    """Build deterministic query text for template selection.

    Args:
        domain: Training domain (e.g., "running")
        session_type: Session type (e.g., "easy", "threshold", "vo2")
        race_distance: Race distance (e.g., "5k", "marathon") or None
        phase: Training phase (e.g., "build", "taper") or None
        philosophy: Philosophy identifier (e.g., "daniels") or None

    Returns:
        Canonical query text ready for embedding
    """
    parts: list[str] = []

    parts.append(f"{domain} training session")
    parts.append(session_type)

    if race_distance:
        parts.append(f"{race_distance} race")
    if phase:
        parts.append(f"{phase} phase")
    if philosophy:
        parts.append(f"{philosophy} style")

    return " ".join(parts)
