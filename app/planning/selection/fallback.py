"""Deterministic Fallback Selector.

Used when LLM selection fails or is invalid.
Guarantees valid selection with lowest-risk templates.
"""

from app.planning.llm.schemas import DayTemplateCandidates, WeekTemplateSelection


def fallback_select(
    week_index: int,
    candidates: list[DayTemplateCandidates],
) -> WeekTemplateSelection:
    """Fallback template selection using deterministic strategy.

    Strategy:
    - Choose first candidate template per day (lowest risk)
    - Guaranteed to produce valid selection

    Args:
        week_index: Zero-based week index
        candidates: List of day candidates with template options

    Returns:
        WeekTemplateSelection with guaranteed valid selections
    """
    selections: dict[str, str] = {}

    for day_candidates in candidates:
        if not day_candidates.candidate_template_ids:
            continue

        # Strategy: Choose first candidate (lowest risk, deterministic)
        selections[day_candidates.day] = day_candidates.candidate_template_ids[0]

    return WeekTemplateSelection(
        week_index=week_index,
        selections=selections,
    )
