"""Integration Helper for Template Selection.

Wraps async selection logic for sync compiler integration.
Handles LLM selection with fallback.
"""

import asyncio

from loguru import logger

from app.planning.compiler.week_skeleton import Day, DayRole
from app.planning.errors import PlanningInvariantError
from app.planning.library.philosophy import TrainingPhilosophy
from app.planning.library.session_template import SessionTemplate
from app.planning.llm.schemas import DayTemplateCandidates, WeekSelectionInput, WeekTemplateSelection
from app.planning.llm.selector import select_templates
from app.planning.llm.validate import validate_selection
from app.planning.output.models import MaterializedSession, WeekPlan
from app.planning.plan_race import compute_phase
from app.planning.selection.candidate_retriever import get_candidates
from app.planning.selection.fallback import fallback_select


def materialize_sessions_with_templates(
    week_plan: WeekPlan,
    skeleton_days: dict[Day, DayRole],
    allocation: dict[Day, int],
    philosophy: TrainingPhilosophy,
    race_type: str,
    *,
    total_weeks: int,
    all_templates: list[SessionTemplate],
    rag_bias: dict[str, list[str]] | None = None,
    philosophy_summary: str | None = None,
    use_llm: bool = True,
) -> WeekPlan:
    """Materialize sessions with template selection.

    This function:
    1. Gets candidates for each day (deterministic)
    2. Selects templates (LLM or fallback)
    3. Validates selection
    4. Creates new WeekPlan with template IDs

    Args:
        week_plan: Original WeekPlan with UNASSIGNED templates
        skeleton_days: Day role mapping
        allocation: Time allocation per day
        philosophy: Training philosophy
        race_type: Race type
        total_weeks: Total weeks in plan
        all_templates: All available templates
        rag_bias: Optional RAG exclusion context
        philosophy_summary: Optional philosophy summary
        use_llm: Whether to use LLM selection (True) or fallback (False)

    Returns:
        New WeekPlan with template IDs assigned

    Raises:
        PlanningInvariantError: If selection fails validation
    """
    week_index = week_plan.week_index
    phase = compute_phase(week_index + 1, total_weeks)  # week_index is 0-based, compute_phase expects 1-based

    # Build candidates per day
    day_candidates_list: list[DayTemplateCandidates] = []
    session_map: dict[str, MaterializedSession] = {}
    for session in week_plan.sessions:
        day = session.day
        role = skeleton_days.get(day, "rest")
        duration = allocation.get(day, 0)

        if duration == 0:
            continue

        # Get candidates
        candidates = get_candidates(
            day_role=role,
            duration_min=duration,
            philosophy=philosophy,
            race_type=race_type,
            phase=phase,
            all_templates=all_templates,
            rag_bias=rag_bias,
        )

        if not candidates:
            logger.warning(
                "No candidates found for day",
                day=day,
                role=role,
                duration=duration,
                phase=phase,
            )
            continue

        day_candidates_list.append(
            DayTemplateCandidates(
                day=day,
                role=role,
                duration_minutes=duration,
                candidate_template_ids=[t.id for t in candidates],
            )
        )
        session_map[day] = session

    if not day_candidates_list:
        logger.warning("No candidates found for any day, returning original plan")
        return week_plan

    # Build selection input
    selection_input = WeekSelectionInput(
        week_index=week_index,
        race_type=race_type,
        phase=phase,
        philosophy_id=philosophy.id,
        days=day_candidates_list,
    )

    # Select templates (LLM or fallback)
    selection: WeekTemplateSelection
    if use_llm:
        try:
            # Run async selection
            selection = asyncio.run(select_templates(selection_input, philosophy_summary))
            # Validate selection
            validate_selection(selection, day_candidates_list)
            logger.debug(
                "Template selection completed (LLM)",
                week_index=week_index,
                selections_count=len(selection.selections),
            )
        except (RuntimeError, Exception) as e:
            # RuntimeError: Event loop already running
            # Other exceptions: LLM call failed
            logger.warning(
                "LLM selection failed, using fallback",
                week_index=week_index,
                error=str(e),
                exc_info=True,
            )
            selection = fallback_select(week_index, day_candidates_list)
    else:
        selection = fallback_select(week_index, day_candidates_list)
        logger.debug(
            "Template selection completed (fallback)",
            week_index=week_index,
            selections_count=len(selection.selections),
        )

    # Build new sessions with template IDs
    new_sessions: list[MaterializedSession] = []
    for day, template_id in selection.selections.items():
        if day not in session_map:
            continue
        original_session = session_map[day]
        new_sessions.append(
            MaterializedSession(
                day=original_session.day,
                session_template_id=template_id,
                session_type=original_session.session_type,
                duration_minutes=original_session.duration_minutes,
                distance_miles=original_session.distance_miles,
                notes=original_session.notes,
            )
        )

    # Preserve sessions without selections
    new_sessions.extend(
        session for session in week_plan.sessions if session.day not in selection.selections
    )

    return WeekPlan(
        week_index=week_plan.week_index,
        sessions=new_sessions,
        total_duration_min=week_plan.total_duration_min,
        total_distance_miles=week_plan.total_distance_miles,
    )
