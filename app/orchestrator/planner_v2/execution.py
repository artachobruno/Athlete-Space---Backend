"""B8.2 — Step registry and execution.

This module defines the explicit, ordered step registry and provides
thin wrapper functions that call B2-B7 implementations.
"""

from dataclasses import replace
from datetime import date, timedelta

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.orchestrator.planner_v2.errors import StepExecutionError, ValidationError
from app.orchestrator.planner_v2.progress import emit_step_complete, emit_step_failed, emit_step_start
from app.orchestrator.planner_v2.state import PlannerV2State
from app.planner.calendar_persistence import persist_plan
from app.planner.macro_plan import generate_macro_plan
from app.planner.models import (
    DistributedDay,
    PlanContext,
    PlannedWeek,
    PlanRuntimeContext,
    WeekStructure,
)
from app.planner.philosophy_selector import select_philosophy
from app.planner.session_template_selector import select_templates_for_week
from app.planner.session_text_generator import generate_week_sessions
from app.planner.volume_allocator import allocate_week_volume
from app.planner.week_structure import load_week_structure

# Step registry (explicit, ordered)
PLANNER_V2_STEPS = [
    "macro_plan",  # B2
    "philosophy",  # B2.5
    "structure",  # B3
    "volume",  # B4
    "templates",  # B5
    "session_text",  # B6
    "persist",  # B7
]

# Progress percentages per step
STEP_PERCENTS = {
    "macro_plan": 10,
    "philosophy": 20,
    "structure": 35,
    "volume": 45,
    "templates": 60,
    "session_text": 80,
    "persist": 100,
}


def _compute_days_to_race(ctx, week) -> int:
    """Compute days to race for a given week.

    For race plans, calculates days from week start to race date.
    For season plans, returns a large number (9999) to match any structure.

    Args:
        ctx: Plan context with target_date
        week: Macro week with week_index

    Returns:
        Days until race (or 9999 for season plans)
    """
    if not ctx.target_date:
        return 9999  # Season / non-race plans

    race_date = date.fromisoformat(ctx.target_date)
    weeks_until_race = ctx.weeks - week.week_index + 1
    week_start = race_date - timedelta(weeks=weeks_until_race)

    return (race_date - week_start).days


async def run_b2_macro_plan(state: PlannerV2State) -> PlannerV2State:
    """Run B2: Macro plan generation.

    Args:
        state: Current planner state

    Returns:
        Updated state with macro_plan set

    Raises:
        StepExecutionError: If macro plan generation fails
    """
    if state.macro_plan is not None:
        logger.warning("B2 already completed, skipping")
        return state

    start_time = emit_step_start(state.plan_id, "macro_plan", STEP_PERCENTS["macro_plan"])

    try:
        macro_weeks = await generate_macro_plan(state.ctx, state.athlete_state)

        summary = {
            "weeks": len(macro_weeks),
            "first_focus": macro_weeks[0].focus.value if macro_weeks else None,
        }

        emit_step_complete(state.plan_id, "macro_plan", STEP_PERCENTS["macro_plan"], start_time, summary)

        return replace(state, macro_plan=macro_weeks, current_step="macro_plan")
    except Exception as e:
        emit_step_failed(state.plan_id, "macro_plan", start_time, str(e))
        raise StepExecutionError("macro_plan", e) from e


def run_b2_5_philosophy(state: PlannerV2State) -> PlannerV2State:
    """Run B2.5: Philosophy selection.

    Args:
        state: Current planner state (must have macro_plan)

    Returns:
        Updated state with philosophy_id and structure set

    Raises:
        StepExecutionError: If philosophy selection fails
        ValidationError: If macro_plan is missing
    """
    if state.philosophy_id is not None:
        logger.warning("B2.5 already completed, skipping")
        return state

    if state.macro_plan is None:
        raise ValidationError("B2.5 requires macro_plan from B2")

    start_time = emit_step_start(state.plan_id, "philosophy", STEP_PERCENTS["philosophy"])

    try:
        philosophy = select_philosophy(
            ctx=state.ctx,
            athlete_state=state.athlete_state,
            user_preference=None,  # TODO: Add user preference support
        )

        summary: dict[str, object] = {
            "philosophy_id": philosophy.philosophy_id,
            "domain": philosophy.domain,
            "audience": philosophy.audience,
        }

        emit_step_complete(state.plan_id, "philosophy", STEP_PERCENTS["philosophy"], start_time, summary)

        runtime_ctx = PlanRuntimeContext(plan=state.ctx, philosophy=philosophy)

        return replace(
            state,
            philosophy_id=philosophy.philosophy_id,
            structure=runtime_ctx,
            current_step="philosophy",
        )
    except Exception as e:
        emit_step_failed(state.plan_id, "philosophy", start_time, str(e))
        raise StepExecutionError("philosophy", e) from e


def run_b3_structure(state: PlannerV2State) -> PlannerV2State:
    """Run B3: Week structure loading.

    Args:
        state: Current planner state (must have structure and macro_plan)

    Returns:
        Updated state with week_structures set

    Raises:
        StepExecutionError: If structure loading fails
        ValidationError: If prerequisites are missing
    """
    if state.week_structures is not None:
        logger.warning("B3 already completed, skipping")
        return state

    if state.macro_plan is None:
        raise ValidationError("B3 requires macro_plan from B2")
    if state.structure is None:
        raise ValidationError("B3 requires structure from B2.5")

    start_time = emit_step_start(state.plan_id, "structure", STEP_PERCENTS["structure"])

    try:
        week_structures: list[WeekStructure] = []

        for week in state.macro_plan:
            days_to_race = _compute_days_to_race(state.ctx, week)

            structure = load_week_structure(
                ctx=state.structure,
                week=week,
                athlete_state=state.athlete_state,
                days_to_race=days_to_race,
            )

            week_structures.append(structure)

        summary = {
            "week_count": len(week_structures),
            "philosophy_id": state.philosophy_id,
        }

        emit_step_complete(state.plan_id, "structure", STEP_PERCENTS["structure"], start_time, summary)

        return replace(state, week_structures=week_structures, current_step="structure")
    except Exception as e:
        emit_step_failed(state.plan_id, "structure", start_time, str(e))
        raise StepExecutionError("structure", e) from e


def run_b4_volume(state: PlannerV2State) -> PlannerV2State:
    """Run B4: Volume distribution.

    Args:
        state: Current planner state (must have week_structures and macro_plan)

    Returns:
        Updated state with distributed_days_by_week set

    Raises:
        StepExecutionError: If volume allocation fails
        ValidationError: If prerequisites are missing
    """
    if state.distributed_days_by_week is not None:
        logger.warning("B4 already completed, skipping")
        return state

    if state.week_structures is None:
        raise ValidationError("B4 requires week_structures from B3")
    if state.macro_plan is None:
        raise ValidationError("B4 requires macro_plan from B2")

    start_time = emit_step_start(state.plan_id, "volume", STEP_PERCENTS["volume"])

    try:
        distributed_days_by_week: list[list[DistributedDay]] = []

        for macro_week, week_structure in zip(state.macro_plan, state.week_structures, strict=False):
            # Allocate volume
            distributed_days = allocate_week_volume(
                weekly_distance=macro_week.total_distance,
                structure=week_structure,
            )
            distributed_days_by_week.append(distributed_days)

        summary: dict[str, object] = {
            "week_count": len(distributed_days_by_week),
            "total_days": sum(len(days) for days in distributed_days_by_week),
        }

        emit_step_complete(state.plan_id, "volume", STEP_PERCENTS["volume"], start_time, summary)

        return replace(state, distributed_days_by_week=distributed_days_by_week, current_step="volume")
    except Exception as e:
        emit_step_failed(state.plan_id, "volume", start_time, str(e))
        raise StepExecutionError("volume", e) from e


def run_b5_templates(state: PlannerV2State) -> PlannerV2State:
    """Run B5: Template selection.

    Args:
        state: Current planner state (must have distributed_days_by_week and structure)

    Returns:
        Updated state with templated_weeks set

    Raises:
        StepExecutionError: If template selection fails
        ValidationError: If prerequisites are missing
    """
    if state.templated_weeks is not None:
        logger.warning("B5 already completed, skipping")
        return state

    if state.distributed_days_by_week is None:
        raise ValidationError("B5 requires distributed_days_by_week from B4")
    if state.structure is None:
        raise ValidationError("B5 requires structure from B2.5")
    if state.week_structures is None:
        raise ValidationError("B5 requires week_structures from B3")
    if state.macro_plan is None:
        raise ValidationError("B5 requires macro_plan from B2")

    start_time = emit_step_start(state.plan_id, "templates", STEP_PERCENTS["templates"])

    try:
        templated_weeks: list[PlannedWeek] = []

        for macro_week, distributed_days, week_structure in zip(
            state.macro_plan, state.distributed_days_by_week, state.week_structures, strict=False
        ):
            # Determine phase from week focus
            phase = "taper" if macro_week.focus.value in {"taper", "sharpening"} else "build"

            # Select templates for this week
            planned_sessions = select_templates_for_week(
                context=state.structure,
                week_index=macro_week.week_index,
                phase=phase,
                days=distributed_days,
                day_index_to_session_type=week_structure.day_index_to_session_type,
            )

            templated_week = PlannedWeek(
                week_index=macro_week.week_index,
                focus=macro_week.focus,
                sessions=planned_sessions,
            )
            templated_weeks.append(templated_week)

        summary = {
            "weeks": len(templated_weeks),
            "philosophy": state.philosophy_id,
        }

        emit_step_complete(state.plan_id, "templates", STEP_PERCENTS["templates"], start_time, summary)

        return replace(state, templated_weeks=templated_weeks, current_step="templates")
    except Exception as e:
        emit_step_failed(state.plan_id, "templates", start_time, str(e))
        raise StepExecutionError("templates", e) from e


async def run_b6_session_text(state: PlannerV2State) -> PlannerV2State:
    """Run B6: Session text generation.

    Args:
        state: Current planner state (must have templated_weeks and structure)

    Returns:
        Updated state with text_weeks set

    Raises:
        StepExecutionError: If session text generation fails
        ValidationError: If prerequisites are missing
    """
    if state.text_weeks is not None:
        logger.warning("B6 already completed, skipping")
        return state

    if state.templated_weeks is None:
        raise ValidationError("B6 requires templated_weeks from B5")
    if state.structure is None:
        raise ValidationError("B6 requires structure from B2.5")

    start_time = emit_step_start(state.plan_id, "session_text", STEP_PERCENTS["session_text"])

    try:
        text_weeks: list[PlannedWeek] = []

        for templated_week in state.templated_weeks:
            # Determine phase from week focus
            phase = "taper" if templated_week.focus.value in {"taper", "sharpening"} else "build"

            # Build context for session text generation
            context = {
                "philosophy_id": state.philosophy_id or "",
                "race_distance": state.ctx.race_distance.value if state.ctx.race_distance else "",
                "phase": phase,
                "week_index": templated_week.week_index,
            }

            # Generate session text for this week
            text_week = await generate_week_sessions(templated_week, context)
            text_weeks.append(text_week)

        summary: dict[str, object] = {
            "weeks": len(text_weeks),
            "sessions_with_text": sum(
                sum(1 for s in w.sessions if s.text_output is not None) for w in text_weeks
            ),
        }

        emit_step_complete(state.plan_id, "session_text", STEP_PERCENTS["session_text"], start_time, summary)

        return replace(state, text_weeks=text_weeks, current_step="session_text")
    except Exception as e:
        emit_step_failed(state.plan_id, "session_text", start_time, str(e))
        raise StepExecutionError("session_text", e) from e


def run_b7_persist(
    state: PlannerV2State,
    user_id: str,
    athlete_id: int,
) -> PlannerV2State:
    """Run B7: Calendar persistence.

    Args:
        state: Current planner state (must have text_weeks)
        user_id: User ID for persistence
        athlete_id: Athlete ID for persistence

    Returns:
        Updated state with persist_result set

    Raises:
        StepExecutionError: If persistence fails
        ValidationError: If prerequisites are missing
    """
    if state.persist_result is not None:
        logger.warning("B7 already completed, skipping")
        return state

    if state.text_weeks is None:
        raise ValidationError("B7 requires text_weeks from B6")
    if state.structure is None:
        raise ValidationError("B7 requires structure from B2.5")

    start_time = emit_step_start(state.plan_id, "persist", STEP_PERCENTS["persist"])

    try:
        # Update ctx with philosophy before persistence
        ctx_with_philosophy = replace(state.ctx, philosophy=state.structure.philosophy)

        persist_result = persist_plan(
            ctx=ctx_with_philosophy,
            weeks=state.text_weeks,
            user_id=user_id,
            athlete_id=athlete_id,
            plan_id=state.plan_id,
        )

        summary: dict[str, object] = {
            "created": persist_result.created,
            "updated": persist_result.updated,
            "skipped": persist_result.skipped,
            "warnings": len(persist_result.warnings),
        }

        emit_step_complete(state.plan_id, "persist", STEP_PERCENTS["persist"], start_time, summary)

        return replace(state, persist_result=persist_result, current_step="persist")
    except Exception as e:
        emit_step_failed(state.plan_id, "persist", start_time, str(e))
        # Persistence failures are non-fatal (partial success allowed)
        logger.warning("B7 persistence failed but continuing", error=str(e))
        raise StepExecutionError("persist", e) from e


async def run_step(step: str, state: PlannerV2State, user_id: str | None = None, athlete_id: int | None = None) -> PlannerV2State:
    """Run a single step by name.

    Args:
        step: Step name (must be in PLANNER_V2_STEPS)
        state: Current planner state
        user_id: Optional user ID (required for B7)
        athlete_id: Optional athlete ID (required for B7)

    Returns:
        Updated state after step execution

    Raises:
        ValueError: If step name is invalid
        StepExecutionError: If step execution fails
    """
    if step not in PLANNER_V2_STEPS:
        raise ValueError(f"Unknown step: {step}. Must be one of {PLANNER_V2_STEPS}")

    if step == "macro_plan":
        return await run_b2_macro_plan(state)
    if step == "philosophy":
        return run_b2_5_philosophy(state)
    if step == "structure":
        return run_b3_structure(state)
    if step == "volume":
        return run_b4_volume(state)
    if step == "templates":
        return run_b5_templates(state)
    if step == "session_text":
        return await run_b6_session_text(state)
    if step == "persist":
        if user_id is None or athlete_id is None:
            raise ValueError("B7 requires user_id and athlete_id")
        return run_b7_persist(state, user_id, athlete_id)

    raise ValueError(f"Step '{step}' not implemented")
