"""Canonical text construction for week structure embeddings.

This module builds deterministic, stable text representations of training
week structures for semantic retrieval. The canonical text format is designed
to be embedded and compared via cosine similarity.
"""

from app.planning.structure.types import StructureSpec


def build_week_structure_canonical_text(spec: StructureSpec) -> str:
    """Build canonical text representation of a week structure for embedding.

    The canonical text is deterministic, stable, and includes both metadata
    and structure details in a single searchable string.

    Args:
        spec: Parsed structure specification

    Returns:
        Canonical text string ready for embedding
    """
    lines: list[str] = []

    # Header
    lines.append("Training week structure.")

    # Core metadata (inline)
    lines.append(f"Phase: {spec.metadata.phase}")
    lines.append(f"Focus: {spec.metadata.phase}")  # Phase is the focus indicator
    lines.append(f"Audience: {spec.metadata.audience}")

    # Days per week
    days_count = len(spec.week_pattern)
    lines.append(f"Days per week: {days_count}")

    # Race distance
    if spec.metadata.race_types:
        race_types_str = ", ".join(spec.metadata.race_types)
        lines.append(f"Race distance: {race_types_str}")
    else:
        lines.append("Race distance: all")

    # Days to race range
    lines.append(f"Days to race range: {spec.metadata.days_to_race_min}-{spec.metadata.days_to_race_max}")

    # Week intent from notes
    if spec.notes and "intent" in spec.notes:
        lines.append("")
        lines.append("Week intent:")
        intent = spec.notes["intent"].strip()
        # Split into sentences or bullets
        for raw_sentence in intent.split(". "):
            stripped_sentence = raw_sentence.strip()
            if stripped_sentence:
                lines.append(f"  - {stripped_sentence}")

    # Typical sessions from week pattern
    lines.append("")
    lines.append("Typical sessions:")
    session_types = set(spec.week_pattern.values())
    session_descriptions = {
        "easy": "Easy aerobic run",
        "easy_plus_strides": "Easy run with strides",
        "threshold": "Threshold/tempo run",
        "vo2": "VO2max intervals",
        "long": "Long run",
        "rest": "Rest day",
        "race": "Race day",
        "cross": "Cross training",
    }
    for session_type in sorted(session_types):
        description = session_descriptions.get(session_type, session_type)
        count = sum(1 for v in spec.week_pattern.values() if v == session_type)
        if count > 1:
            lines.append(f"  - {description} ({count}x per week)")
        else:
            lines.append(f"  - {description}")

    # Session groups summary
    if spec.session_groups:
        lines.append("")
        lines.append("Session groups:")
        for group_name, session_list in spec.session_groups.items():
            sessions_str = ", ".join(session_list)
            lines.append(f"  {group_name}: {sessions_str}")

    # Rules summary
    if spec.rules:
        lines.append("")
        lines.append("Training rules:")
        if "hard_days_max" in spec.rules:
            lines.append(f"  - Maximum hard days: {spec.rules['hard_days_max']}")
        if "no_consecutive_hard_days" in spec.rules and spec.rules.get("no_consecutive_hard_days"):
            lines.append("  - No consecutive hard days")
        if "long_run" in spec.rules:
            long_run_info = spec.rules["long_run"]
            if isinstance(long_run_info, dict):
                if "required_count" in long_run_info:
                    lines.append(f"  - Long runs required: {long_run_info['required_count']}")
                if "preferred_day" in long_run_info:
                    lines.append(f"  - Preferred long run day: {long_run_info['preferred_day']}")

    # Guards summary
    if spec.guards:
        lines.append("")
        lines.append("Guardrails:")
        for guard_key, guard_value in spec.guards.items():
            lines.append(f"  - {guard_key}: {guard_value}")

    return "\n".join(lines)
