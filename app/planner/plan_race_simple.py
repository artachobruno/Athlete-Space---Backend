"""CANONICAL PLANNER — DO NOT DUPLICATE.

Complete linear planner pipeline entry point (B2 → B7).

This is the ONLY planner entry point. All planning traffic must flow through this function.

Pipeline stages:
- B2: Macro plan generation (LLM-based, single call)
- B2.5: Philosophy selection (deterministic)
- B3: Week structure loading (RAG-backed, deterministic)
- B4: Volume allocation (deterministic)
- B5: Template selection (deterministic, RAG-backed)
- B6: Session text generation (LLM-based, cached)
- B7: Calendar persistence (idempotent)

No recursion. No repair. No retries. No mutations after generation.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.enums import PlanType, RaceDistance, TrainingIntent, WeekFocus
from app.domains.training_plan.guards import (
    assert_new_planner_only,
    assert_planner_v2_only,
    guard_invariants,
    guard_no_recursion,
    guard_no_repair,
    log_planner_v2_entry,
)
from app.domains.training_plan.models import (
    MacroWeek,
    PlanContext,
    PlannedSession,
    PlannedWeek,
    PlanRuntimeContext,
    WeekStructure,
)
from app.domains.training_plan.observability import (
    PlannerStage,
    log_event,
    log_stage_event,
    log_stage_metric,
    timing,
)
from app.domains.training_plan.plan_pipeline import build_plan_structure
from app.domains.training_plan.session_template_selector import select_templates_for_week
from app.domains.training_plan.session_text_generator import generate_session_text
from app.domains.training_plan.volume_allocator import allocate_week_volume
from app.planner.calendar_persistence import PersistResult, persist_plan


async def _generate_week_with_text(
    *,
    week_idx: int,
    structure: WeekStructure,
    planned_sessions: list[PlannedSession],
    runtime_ctx: PlanRuntimeContext,
    phase: str,
    plan_id: str,
) -> PlannedWeek:
    """Generate session text for a week and return PlannedWeek.

    Parallelizes session text generation within the week for better performance.

    Args:
        week_idx: Week index (0-based)
        structure: Week structure
        planned_sessions: Sessions with templates
        runtime_ctx: Runtime context
        phase: Training phase
        plan_id: Plan ID for logging

    Returns:
        PlannedWeek with sessions that have text_output set
    """
    log_stage_event(PlannerStage.TEXT, "start", plan_id, {"week_index": week_idx + 1})
    try:
        with timing("planner.stage.text"):
            context_dict = {
                "philosophy_id": runtime_ctx.philosophy.philosophy_id,
                "race_distance": runtime_ctx.plan.race_distance.value if runtime_ctx.plan.race_distance else "season",
                "phase": phase,
                "week_index": week_idx + 1,
            }

            # Parallelize session text generation within the week
            # Semaphore is handled inside generate_session_text to limit concurrent LLM calls
            async def generate_session_with_text(session: PlannedSession) -> PlannedSession:
                session_text = await generate_session_text(session, context_dict)
                return session.with_text(session_text)

            # Generate all session texts in parallel (limited by semaphore)
            sessions_with_text = await asyncio.gather(
                *[generate_session_with_text(session) for session in planned_sessions]
            )

            planned_week = PlannedWeek(
                week_index=week_idx + 1,
                focus=structure.focus,
                sessions=list(sessions_with_text),
            )
        log_stage_event(
            PlannerStage.TEXT,
            "success",
            plan_id,
            {"week_index": week_idx + 1, "session_count": len(sessions_with_text)},
        )
        log_stage_metric(PlannerStage.TEXT, True)
    except Exception as e:
        log_stage_event(PlannerStage.TEXT, "fail", plan_id, {"week_index": week_idx + 1, "error": str(e)})
        log_stage_metric(PlannerStage.TEXT, False)
        raise
    else:
        return planned_week


async def execute_canonical_pipeline(
    ctx: PlanContext,
    athlete_state: AthleteState,
    user_id: str,
    athlete_id: int,
    plan_id: str,
    *,
    progress_callback: Callable[[int, int, str], Awaitable[None] | None] | None = None,
    base_volume_calculator: Callable[[int], float] | None = None,
) -> tuple[list[PlannedWeek], PersistResult]:
    """Execute the canonical planner pipeline (B2 → B7).

    This is the shared core pipeline used by all plan types (race, season, week).

    Args:
        ctx: Plan context
        athlete_state: Athlete state snapshot
        user_id: User ID
        athlete_id: Athlete ID
        plan_id: Plan ID for correlation
        progress_callback: Optional callback(week_number, total_weeks, phase) for progress tracking
        base_volume_calculator: Optional function(week_idx) -> volume for volume calculation

    Returns:
        Tuple of (list of PlannedWeek objects, PersistResult)

    Raises:
        RuntimeError: If planning fails at any stage
    """
    # B2 → B2.5 → B3: Build plan structure
    logger.debug("B2-B3: Building plan structure")
    runtime_ctx, week_structures = await build_plan_structure(
        ctx=ctx,
        athlete_state=athlete_state,
        user_preference=None,
    )

    # B4: Allocate volume for each week (parallelized)
    log_stage_event(PlannerStage.VOLUME, "start", plan_id)
    try:
        with timing("planner.stage.volume"):
            def allocate_week_volume_sync(week_idx: int, structure: WeekStructure) -> list:
                """Allocate volume for a single week (synchronous helper for parallelization)."""
                # Use custom calculator if provided, otherwise use default
                if base_volume_calculator:
                    week_volume = base_volume_calculator(week_idx)
                else:
                    # Default: simple progression
                    base_volume = 40.0
                    week_volume = base_volume + (week_idx * 2.0)
                return allocate_week_volume(
                    weekly_distance=week_volume,
                    structure=structure,
                )

            # Parallelize volume allocation across weeks
            loop = asyncio.get_event_loop()
            distributed_weeks = await asyncio.gather(
                *[
                    loop.run_in_executor(None, allocate_week_volume_sync, week_idx, structure)
                    for week_idx, structure in enumerate(week_structures)
                ]
            )
        log_stage_event(PlannerStage.VOLUME, "success", plan_id, {"week_count": len(distributed_weeks)})
        log_stage_metric(PlannerStage.VOLUME, True)
        log_event("volume_allocated", week_count=len(distributed_weeks), plan_id=plan_id)
    except Exception as e:
        log_stage_event(PlannerStage.VOLUME, "fail", plan_id, {"error": str(e)})
        log_stage_metric(PlannerStage.VOLUME, False)
        raise

    # B5: Select templates for each week (parallelized)
    # B6: Generate session text (parallelized)
    log_stage_event(PlannerStage.TEMPLATE, "start", plan_id)
    try:
        with timing("planner.stage.template"):
            async def process_week(
                week_idx: int,
                structure: WeekStructure,
                distributed_days: list,
            ) -> PlannedWeek:
                """Process a single week: template selection + text generation."""
                try:
                    if progress_callback:
                        total_weeks = ctx.weeks
                        phase_str = "base" if week_idx < total_weeks * 0.5 else ("build" if week_idx < total_weeks * 0.8 else "peak")
                        result = progress_callback(week_idx + 1, total_weeks, phase_str)
                        if isinstance(result, Awaitable):
                            await result

                    # Determine phase from structure focus
                    phase = "taper" if structure.focus in {WeekFocus.TAPER, WeekFocus.SHARPENING} else "build"

                    # B5: Select templates (synchronous, run in executor for parallelization)
                    loop = asyncio.get_event_loop()
                    planned_sessions = await loop.run_in_executor(
                        None,
                        select_templates_for_week,
                        runtime_ctx,
                        week_idx + 1,
                        phase,
                        distributed_days,
                        structure.day_index_to_session_type,
                    )

                    # B6: Generate session text (already parallelized within week)
                    return await _generate_week_with_text(
                        week_idx=week_idx,
                        structure=structure,
                        planned_sessions=planned_sessions,
                        runtime_ctx=runtime_ctx,
                        phase=phase,
                        plan_id=plan_id,
                    )
                except Exception as e:
                    # Add week context to error for better debugging
                    logger.error(
                        "Failed to process week",
                        week_index=week_idx + 1,
                        phase=phase if "phase" in locals() else "unknown",
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    raise

            # Process all weeks in parallel
            planned_weeks = await asyncio.gather(
                *[
                    process_week(week_idx, structure, distributed_days)
                    for week_idx, (structure, distributed_days) in enumerate(zip(week_structures, distributed_weeks, strict=False))
                ]
            )
            # Sort by week_index to maintain order
            planned_weeks = sorted(planned_weeks, key=lambda w: w.week_index)
        log_stage_event(PlannerStage.TEMPLATE, "success", plan_id, {"week_count": len(planned_weeks)})
        log_stage_metric(PlannerStage.TEMPLATE, True)
        log_event("template_selected", week_count=len(planned_weeks), plan_id=plan_id)
    except Exception as e:
        log_stage_event(PlannerStage.TEMPLATE, "fail", plan_id, {"error": str(e)})
        log_stage_metric(PlannerStage.TEMPLATE, False)
        raise

    # Guard invariants before persistence
    # Extract planned_sessions for guard check
    # Filter out sessions with distance <= 0 (rest days, race days)
    all_planned_sessions: list[PlannedSession] = []
    for week in planned_weeks:
        all_planned_sessions.extend(
            session
            for session in week.sessions
            if isinstance(session, PlannedSession) and session.distance > 0
        )

    # Note: macro_weeks check is skipped if list is empty (will be added when available from build_plan_structure)
    guard_invariants([], all_planned_sessions)

    # B7: Persist to calendar
    log_stage_event(PlannerStage.PERSIST, "start", plan_id)
    try:
        with timing("planner.stage.persist"):
            # Create PlanContext with philosophy set (required for persistence)
            ctx_with_philosophy = PlanContext(
                plan_type=runtime_ctx.plan.plan_type,
                intent=runtime_ctx.plan.intent,
                weeks=runtime_ctx.plan.weeks,
                race_distance=runtime_ctx.plan.race_distance,
                target_date=runtime_ctx.plan.target_date,
                philosophy=runtime_ctx.philosophy,
            )
            persist_result: PersistResult = persist_plan(
                ctx=ctx_with_philosophy,
                weeks=planned_weeks,
                user_id=user_id,
                athlete_id=athlete_id,
                plan_id=plan_id,
            )
        log_stage_event(
            PlannerStage.PERSIST,
            "success",
            plan_id,
            {
                "created": persist_result.created,
                "updated": persist_result.updated,
                "skipped": persist_result.skipped,
            },
        )
        log_stage_metric(PlannerStage.PERSIST, True)
        log_event(
            "calendar_persisted",
            plan_id=plan_id,
            created=persist_result.created,
            updated=persist_result.updated,
            skipped=persist_result.skipped,
        )
    except Exception as e:
        log_stage_event(PlannerStage.PERSIST, "fail", plan_id, {"error": str(e)})
        log_stage_metric(PlannerStage.PERSIST, False)
        raise

    return planned_weeks, persist_result


def _map_distance_string_to_enum(distance: str) -> RaceDistance:
    """Map distance string to RaceDistance enum.

    Args:
        distance: Distance string ("5K", "10K", "Half Marathon", "Marathon", "Ultra")

    Returns:
        RaceDistance enum value

    Raises:
        ValueError: If distance string is invalid
    """
    mapping = {
        "5K": RaceDistance.FIVE_K,
        "10K": RaceDistance.TEN_K,
        "Half Marathon": RaceDistance.HALF_MARATHON,
        "Marathon": RaceDistance.MARATHON,
        "Ultra": RaceDistance.ULTRA,
    }
    if distance not in mapping:
        raise ValueError(f"Invalid race distance: {distance}. Must be one of {list(mapping.keys())}")
    return mapping[distance]


async def plan_race_simple(
    race_date: datetime,
    distance: str,
    user_id: str,
    athlete_id: int,
    *,
    start_date: datetime | None = None,
    athlete_state: AthleteState | None = None,
    progress_callback: Callable[[int, int, str], Awaitable[None] | None] | None = None,
) -> tuple[list[dict], int]:
    """Generate complete race plan using linear pipeline (B2 → B7).

    This is the ONLY planner entry point. All planning must use this function.

    Args:
        race_date: Race date
        distance: Race distance ("5K", "10K", "Half Marathon", "Marathon", "Ultra")
        user_id: User ID
        athlete_id: Athlete ID
        start_date: Training start date (optional, defaults to 16 weeks before race)
        athlete_state: Athlete state snapshot (optional, will use defaults if None)
        progress_callback: Optional callback(week_number, total_weeks, phase) for progress tracking

    Returns:
        Tuple of (list of session dictionaries, total weeks)

    Raises:
        RuntimeError: If planning fails at any stage
    """
    # Guards: Prevent legacy paths and forbidden behaviors
    assert_new_planner_only()
    assert_planner_v2_only()
    guard_no_recursion(0)  # Entry point has depth 0
    # Convert flags list to dict format expected by guard
    flags_dict: dict[str, bool | str | int | float] = {}
    if athlete_state and athlete_state.flags:
        flags_dict = dict.fromkeys(athlete_state.flags, True)
    guard_no_repair(flags_dict)

    # Log entry point for monitoring
    log_planner_v2_entry()

    # Generate plan_id for correlation
    plan_id = str(uuid.uuid4())

    logger.info(
        "planner_v2_entry: Starting race plan generation",
        distance=distance,
        race_date=race_date.isoformat(),
        user_id=user_id,
        athlete_id=athlete_id,
        plan_id=plan_id,
    )

    # Compute start date and total weeks
    if start_date is None:
        start_date = race_date - timedelta(weeks=16)

    total_weeks = int((race_date.date() - start_date.date()).days / 7)
    if total_weeks < 4:
        total_weeks = 16
        start_date = race_date - timedelta(weeks=16)

    # Create plan context
    race_distance_enum = _map_distance_string_to_enum(distance)
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=total_weeks,
        race_distance=race_distance_enum,
        target_date=race_date.date().isoformat(),
    )

    # Use default athlete state if not provided
    if athlete_state is None:
        athlete_state = AthleteState(
            ctl=50.0,
            atl=45.0,
            tsb=5.0,
            load_trend="stable",
            volatility="low",
            days_since_rest=2,
            days_to_race=None,
            seven_day_volume_hours=5.0,
            fourteen_day_volume_hours=10.0,
            flags=[],
            confidence=0.9,
        )

    # Use canonical pipeline
    def volume_calculator(week_idx: int) -> float:
        """Calculate volume for a week based on distance."""
        base_volume = 50.0 if distance == "Marathon" else 40.0
        return base_volume + (week_idx * 2.0)  # Simple progression

    planned_weeks, persist_result = await execute_canonical_pipeline(
        ctx=ctx,
        athlete_state=athlete_state,
        user_id=user_id,
        athlete_id=athlete_id,
        plan_id=plan_id,
        progress_callback=progress_callback,
        base_volume_calculator=volume_calculator,
    )

    # Convert to legacy session dict format for compatibility
    all_sessions = []
    plan_start = start_date.date()
    days_since_monday = plan_start.weekday()
    monday_start = plan_start - timedelta(days=days_since_monday)

    for week in planned_weeks:
        for session in week.sessions:
            session_date = monday_start + timedelta(
                weeks=week.week_index - 1,
                days=session.day_index,
            )
            session_date_dt = datetime.combine(session_date, datetime.min.time()).replace(tzinfo=timezone.utc)

            # Extract title and description from text_output
            text_output = session.text_output
            title = text_output.title if text_output else f"{session.day_type.value.title()} Run"
            description = text_output.description if text_output else ""

            # Extract distance in km
            distance_km = None
            if text_output and "total_distance_mi" in text_output.computed:
                computed_dist = text_output.computed["total_distance_mi"]
                if isinstance(computed_dist, (int, float)):
                    distance_km = float(computed_dist) * 1.60934  # Convert miles to km
            if distance_km is None and session.distance > 0:
                # Fallback: use session distance (assumed to be in miles)
                distance_km = float(session.distance) * 1.60934

            # Extract duration in minutes
            duration_minutes = None
            if text_output and "total_duration_min" in text_output.computed:
                computed_dur = text_output.computed["total_duration_min"]
                if isinstance(computed_dur, (int, float)):
                    duration_minutes = int(computed_dur)
            if duration_minutes is None and text_output and "intensity_minutes" in text_output.computed:
                intensity = text_output.computed["intensity_minutes"]
                if isinstance(intensity, dict) and "total" in intensity:
                    total = intensity["total"]
                    if isinstance(total, int):
                        duration_minutes = total

            session_dict = {
                "date": session_date_dt,
                "type": "Run",
                "title": title,
                "description": description,
                "distance_km": distance_km,
                "duration_minutes": duration_minutes,
                "intensity": session.day_type.value if hasattr(session, "day_type") else "moderate",
                "notes": description,
                "week_number": week.week_index,
            }
            all_sessions.append(session_dict)

    logger.info(
        "planner_v2_entry: Race plan generation complete",
        distance=distance,
        race_date=race_date.isoformat(),
        total_weeks=total_weeks,
        total_sessions=len(all_sessions),
        user_id=user_id,
        athlete_id=athlete_id,
        persisted_count=persist_result.created,
    )

    return all_sessions, total_weeks
