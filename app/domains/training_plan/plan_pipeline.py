"""Plan structure pipeline (B2 → B2.5 → B3).

This module wires together the planning stages:
- B2: Macro plan generation (LLM-based)
- B2.5: Philosophy selection (deterministic)
- B3: Week structure loading (RAG-backed, deterministic)

After this pipeline:
- Philosophy is locked
- All downstream stages use only structures from selected philosophy
- No cross-philosophy structure leaks
"""

from datetime import date, timedelta

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.macro_plan import generate_macro_plan
from app.domains.training_plan.models import MacroWeek, PlanContext, PlanRuntimeContext, WeekStructure
from app.domains.training_plan.observability import (
    PlannerStage,
    log_event,
    log_stage_event,
    log_stage_metric,
    timing,
)
from app.domains.training_plan.philosophy_selector_semantic import select_philosophy_semantic as select_philosophy
from app.domains.training_plan.week_structure_selector_semantic import load_week_structure_semantic as load_week_structure


def _compute_days_to_race(ctx: PlanContext, week: MacroWeek) -> int:
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
    # Calculate week start: race_date - (total_weeks - week_index + 1) weeks
    weeks_until_race = ctx.weeks - week.week_index + 1
    week_start = race_date - timedelta(weeks=weeks_until_race)

    return (race_date - week_start).days


async def build_plan_structure(
    ctx: PlanContext,
    athlete_state: AthleteState,
    user_preference: str | None = None,
) -> tuple[PlanRuntimeContext, list[WeekStructure]]:
    """Execute B2 → B2.5 → B3 pipeline.

    This function:
    1. Generates macro weeks (B2 - LLM-based)
    2. Selects one philosophy (B2.5 - deterministic)
    3. Loads week structures for each macro week (B3 - RAG-backed)

    After execution:
    - Philosophy is locked
    - All week structures come from selected philosophy namespace
    - No cross-philosophy structure leaks

    Args:
        ctx: Plan context with intent, race_distance, weeks
        athlete_state: Athlete state with metrics and flags
        user_preference: Optional explicit philosophy ID override

    Returns:
        Tuple of:
        - runtime_ctx: PlanRuntimeContext with plan and philosophy
        - week_structures: Ordered list of WeekStructure (one per macro week)

    Raises:
        PlannerError: If any stage fails
    """
    logger.info(
        "Building plan structure pipeline",
        plan_type=ctx.plan_type.value,
        intent=ctx.intent.value,
        race_distance=ctx.race_distance.value if ctx.race_distance else None,
        weeks=ctx.weeks,
    )

    plan_id = None  # TODO: Generate plan_id if available

    # -----------------------
    # B2 — Macro Plan
    # -----------------------
    log_stage_event(PlannerStage.MACRO, "start", plan_id)
    try:
        with timing("planner.stage.macro"):
            macro_weeks = await generate_macro_plan(ctx, athlete_state)
        log_stage_event(PlannerStage.MACRO, "success", plan_id, {"week_count": len(macro_weeks)})
        log_stage_metric(PlannerStage.MACRO, True)
        log_event("macro_plan_generated", week_count=len(macro_weeks), plan_id=plan_id)
    except Exception as e:
        log_stage_event(PlannerStage.MACRO, "fail", plan_id, {"error": str(e)})
        log_stage_metric(PlannerStage.MACRO, False)
        raise

    # -----------------------
    # B2.5 — Philosophy Selection
    # -----------------------
    log_stage_event(PlannerStage.PHILOSOPHY, "start", plan_id)
    try:
        with timing("planner.stage.philosophy"):
            philosophy = select_philosophy(
                ctx=ctx,
                athlete_state=athlete_state,
                user_preference=user_preference,
            )
        log_stage_event(
            PlannerStage.PHILOSOPHY,
            "success",
            plan_id,
            {
                "philosophy_id": philosophy.philosophy_id,
                "domain": philosophy.domain,
                "audience": philosophy.audience,
            },
        )
        log_stage_metric(PlannerStage.PHILOSOPHY, True)
    except Exception as e:
        log_stage_event(PlannerStage.PHILOSOPHY, "fail", plan_id, {"error": str(e)})
        log_stage_metric(PlannerStage.PHILOSOPHY, False)
        raise

    runtime_ctx = PlanRuntimeContext(
        plan=ctx,
        philosophy=philosophy,
    )

    # -----------------------
    # B3 — Week Structures
    # -----------------------
    log_stage_event(PlannerStage.STRUCTURE, "start", plan_id)
    try:
        with timing("planner.stage.structure"):
            week_structures: list[WeekStructure] = []

            for week in macro_weeks:
                days_to_race = _compute_days_to_race(ctx, week)

                structure = load_week_structure(
                    ctx=runtime_ctx,
                    week=week,
                    _athlete_state=athlete_state,
                    days_to_race=days_to_race,
                )

                week_structures.append(structure)
                logger.debug(
                    "B3: Loaded structure for week",
                    week_index=week.week_index,
                    focus=week.focus.value,
                    structure_id=structure.structure_id,
                )
        log_stage_event(
            PlannerStage.STRUCTURE,
            "success",
            plan_id,
            {"week_count": len(week_structures)},
        )
        log_stage_metric(PlannerStage.STRUCTURE, True)
        log_event("week_skeleton_loaded", week_count=len(week_structures), plan_id=plan_id)
    except Exception as e:
        log_stage_event(PlannerStage.STRUCTURE, "fail", plan_id, {"error": str(e)})
        log_stage_metric(PlannerStage.STRUCTURE, False)
        raise

    logger.info(
        "Plan structure pipeline complete",
        philosophy_id=philosophy.philosophy_id,
        week_count=len(week_structures),
    )

    return runtime_ctx, week_structures
