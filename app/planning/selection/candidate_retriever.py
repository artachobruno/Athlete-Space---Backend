"""Deterministic Candidate Template Retrieval.

This module filters session templates deterministically BEFORE LLM selection.
No LLM calls here - pure filtering logic.

Filters are applied in order:
1. session_type matches role
2. duration within bounds
3. race_type compatible
4. phase compatible
5. philosophy hard limits
6. RAG filters (exclusions only, never additions)

RAG RULE:
- RAG can only EXCLUDE templates (via rag_bias), never add them
- Structure and session count are determined BEFORE RAG filtering
- RAG is explanatory context only, never structural authority
"""

from app.planning.compiler.week_skeleton import Day, DayRole
from app.planning.library.philosophy import TrainingPhilosophy
from app.planning.library.session_template import SessionTemplate


def get_candidates(
    day_role: str,
    duration_min: int,
    philosophy: TrainingPhilosophy,
    race_type: str,
    phase: str,
    *,
    all_templates: list[SessionTemplate],
    rag_bias: dict[str, list[str]] | None = None,
) -> list[SessionTemplate]:
    """Get candidate templates for a day using deterministic filters.

    Filters templates in order:
    1. session_type matches role
    2. duration within [min_duration, max_duration]
    3. race_type compatible
    4. phase compatible
    5. philosophy hard limits
    6. RAG filters (exclusions only)

    Args:
        day_role: Day role (easy, hard, long, rest)
        duration_min: Allocated duration in minutes
        philosophy: Training philosophy defining constraints
        race_type: Race type (5k, 10k, half, marathon, custom)
        phase: Training phase (base, build, peak, taper)
        all_templates: All available session templates
        rag_bias: Optional RAG context with exclusion lists (tag -> excluded template IDs)

    Returns:
        List of candidate templates that pass all filters
    """
    candidates = list(all_templates)

    # Filter 1: session_type matches role
    role_to_session_type: dict[str, list[str]] = {
        "easy": ["easy", "recovery"],
        "hard": ["tempo", "interval", "hills"],
        "long": ["long"],
        "rest": ["rest"],
    }
    allowed_types = role_to_session_type.get(day_role, [])
    candidates = [t for t in candidates if t.session_type in allowed_types]

    # Filter 2: duration within bounds
    candidates = [
        t for t in candidates if t.min_duration_min <= duration_min <= t.max_duration_min
    ]

    # Filter 3: race_type compatible
    candidates = [
        t for t in candidates if race_type in t.race_types or "custom" in t.race_types
    ]

    # Filter 4: phase compatible
    candidates = [t for t in candidates if phase in t.phase_tags]

    # Filter 5: philosophy hard limits (if applicable)
    if day_role == "hard" and philosophy.max_hard_days_per_week == 0:
        candidates = []

    # Filter 6: RAG exclusions only (never additions)
    if rag_bias:
        excluded_ids: set[str] = set()
        for excluded_list in rag_bias.values():
            excluded_ids.update(excluded_list)
        candidates = [t for t in candidates if t.id not in excluded_ids]

    return candidates
