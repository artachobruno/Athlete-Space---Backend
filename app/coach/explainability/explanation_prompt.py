"""Prompt builder for revision explanations.

This module builds LLM-safe prompts that avoid exposing internal system details
and focus on human-readable explanations.
"""

from typing import Literal


def build_revision_explanation_prompt(
    revision_type: str,
    deltas: dict,
    affected_range: str,
    constraints_triggered: list[str],
    athlete_context: dict,
) -> str:
    """Build a prompt for explaining a plan revision.

    This prompt is designed to be LLM-safe:
    - No raw SQL
    - No internal IDs
    - No system implementation details
    - Focus on human-readable explanations

    Args:
        revision_type: Type of revision (MODIFY, REGENERATE, ROLLBACK, BLOCKED)
        deltas: Dictionary of changes (field: {old, new})
        affected_range: Date range affected (e.g., "June 15-21, 2026")
        constraints_triggered: List of constraint names that were triggered
        athlete_context: Athlete context (race_date, experience_level, recent_fatigue)

    Returns:
        Formatted prompt string for LLM
    """
    # Format deltas for human readability
    deltas_text = _format_deltas_for_prompt(deltas)

    # Format constraints
    constraints_text = "\n".join(f"- {c}" for c in constraints_triggered) if constraints_triggered else "None"

    # Format athlete context
    context_lines = []
    if athlete_context.get("race_date"):
        context_lines.append(f"Upcoming race: {athlete_context['race_date']}")
    if athlete_context.get("experience_level"):
        context_lines.append(f"Experience level: {athlete_context['experience_level']}")
    if athlete_context.get("recent_fatigue"):
        context_lines.append(f"Recent fatigue indicators: {athlete_context['recent_fatigue']}")
    context_text = "\n".join(context_lines) if context_lines else "Standard training context"

    return f"""You are an elite endurance coach explaining a training plan change to an athlete.

Revision type: {revision_type}
Affected range: {affected_range}

Changes made:
{deltas_text}

Constraints enforced:
{constraints_text}

Athlete context:
{context_text}

Rules:
- Do NOT mention internal system terms (IDs, database fields, technical jargon)
- Be calm, confident, and coach-like in tone
- No apologies - this is a professional coaching decision
- No speculation - only explain what you know from the context
- Explain *why* the change was made, not *how* the system works
- For blocked revisions, explain why the change was prevented
- Keep summary to 1-2 sentences
- Keep rationale to 2-4 sentences
- List safeguards as short phrases (e.g., "Race week protection", "Taper protocol")

Return a structured explanation with:
1. Short summary (1-2 sentences)
2. Detailed rationale (2-4 sentences explaining why)
3. List of safeguards applied (short phrases)
4. Confidence/reassurance statement (optional, 1 sentence)
"""


def _format_deltas_for_prompt(deltas: dict) -> str:
    """Format deltas dictionary for human-readable prompt.

    Args:
        deltas: Dictionary of changes

    Returns:
        Formatted string
    """
    if not deltas:
        return "No changes (blocked revision)"

    lines = []
    if isinstance(deltas, dict):
        # Handle different delta formats
        if "deltas" in deltas and isinstance(deltas["deltas"], list):
            # PlanRevision format with list of RevisionDelta
            for delta in deltas["deltas"]:
                if isinstance(delta, dict):
                    entity = delta.get("entity_type", "session")
                    field = delta.get("field", "unknown")
                    old_val = delta.get("old")
                    new_val = delta.get("new")
                    date_str = delta.get("date", "")
                    if date_str:
                        lines.append(f"{date_str}: {field} changed from {old_val} to {new_val} ({entity})")
                    else:
                        lines.append(f"{field} changed from {old_val} to {new_val} ({entity})")
        else:
            # Simple dict format
            for field, change in deltas.items():
                if isinstance(change, dict) and "old" in change and "new" in change:
                    lines.append(f"{field}: {change['old']} → {change['new']}")
                else:
                    lines.append(f"{field}: {change}")

    if not lines:
        return "Changes applied (details not available)"

    return "\n".join(lines)
