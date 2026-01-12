"""Query embedding construction for semantic retrieval.

This module builds canonical query text from planning context, which is then
embedded and used to search for matching philosophies and week structures.

⚠️ Do not embed raw user text. Embed normalized intent context.
"""

from app.domains.training_plan.models import PlanRuntimeContext


def build_philosophy_query_text(
    domain: str,
    race_distance: str,
    athlete_level: str | None = None,
    weekly_mileage: str | None = None,
    goal: str | None = None,
) -> str:
    """Build canonical query text for philosophy selection.

    Args:
        domain: Training domain ("running" | "ultra")
        race_distance: Race distance (e.g., "5k", "marathon")
        athlete_level: Athlete level ("beginner" | "intermediate" | "advanced")
        weekly_mileage: Weekly mileage level ("low" | "medium" | "high")
        goal: Training goal (e.g., "peak performance", "base building")

    Returns:
        Canonical query text ready for embedding
    """
    lines: list[str] = []

    lines.append("Training plan context.")
    lines.append(f"Race distance: {race_distance}")
    lines.append(f"Domain: {domain}")

    if athlete_level:
        lines.append(f"Athlete level: {athlete_level}")

    if weekly_mileage:
        lines.append(f"Weekly mileage: {weekly_mileage}")

    if goal:
        lines.append(f"Goal: {goal}")

    return "\n".join(lines)


def build_week_structure_query_text(
    ctx: PlanRuntimeContext,
    days_to_race: int,
    current_phase: str | None = None,
) -> str:
    """Build canonical query text for week structure selection.

    Args:
        ctx: Plan runtime context
        days_to_race: Days until race
        current_phase: Current training phase ("base" | "build" | "peak" | "taper")

    Returns:
        Canonical query text ready for embedding
    """
    lines: list[str] = []

    lines.append("Training plan context.")

    if ctx.plan.race_distance:
        lines.append(f"Race distance: {ctx.plan.race_distance.value}")

    lines.append(f"Days to race: {days_to_race}")

    if ctx.philosophy:
        lines.append(f"Athlete level: {ctx.philosophy.audience}")

    if current_phase:
        lines.append(f"Current phase: {current_phase}")

    # Add context about training state if available
    if days_to_race > 60:
        lines.append("Training phase: base building")
    elif days_to_race > 21:
        lines.append("Training phase: build")
    elif days_to_race > 10:
        lines.append("Training phase: peak")
    else:
        lines.append("Training phase: taper")

    return "\n".join(lines)
