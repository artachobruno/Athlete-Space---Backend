"""Phase 2 Integration Function.

This module orchestrates the complete Phase 2 compilation pipeline:
PlanSpec → WeekSkeletons → Time Allocation → Validation → WeekPlans → Template Selection (Phase 4) → Session Materialization (Phase 5)

INVARIANT PIPELINE (Structure Resolution):
StructureResolver → StructureSpecParser → StructureValidator → Frozen StructureSpec → Planner → LLM

RAG RULE:
- RAG is used for explanatory context only (philosophy_summary)
- RAG never determines structure, session count, or placement
- RAG can exclude templates (rag_bias) but never adds them
- Philosophy config (loaded from disk) is the source of truth for constraints
- Structure is determined BEFORE RAG is called

STRUCTURE RULE:
- Structure resolution must happen BEFORE session placement, volume logic, LLM calls
- Once resolved, structure is frozen and immutable
- LLM must not add, remove, or move sessions
"""

from datetime import datetime, timezone

from app.planning.metrics.materialization import log_materialization_metrics

from app.planning.compiler.assemble_week import assemble_week_plan
from app.planning.compiler.skeleton_generator import generate_week_skeletons
from app.planning.compiler.time_allocator import allocate_week_time
from app.planning.compiler.validate_compiled_week import validate_compiled_week
from app.planning.context import PlanningContext
from app.planning.library.philosophy import TrainingPhilosophy
from app.planning.library.session_template import SessionTemplate
from app.planning.materialization.materialize_week import materialize_week
from app.planning.materialization.validate import validate_materialized_sessions
from app.planning.output.models import WeekPlan
from app.planning.phase import resolve_phase
from app.planning.progress.emitter import emit_planning_progress
from app.planning.schemas.plan_spec import PlanSpec
from app.planning.selection.integration import materialize_sessions_with_templates
from app.planning.structure.resolver import resolve_structure
from app.planning.structure.validator import validate_structure


def compile_plan(
    plan_spec: PlanSpec,
    philosophy: TrainingPhilosophy,
    *,
    all_templates: list[SessionTemplate] | None = None,
    rag_bias: dict[str, list[str]] | None = None,
    philosophy_summary: str | None = None,
    use_template_selection: bool = True,
    conversation_id: str | None = None,
) -> list[WeekPlan]:
    """Compile a PlanSpec into fully allocated, invariant-safe WeekPlans.

    Pipeline:
    1. Generate week skeletons (structure first)
    2. Allocate time per day (deterministic math)
    3. Validate each week (Phase 0 invariants)
    4. Assemble WeekPlans (materialized sessions)
    5. Select templates (Phase 4) if templates provided
    6. Materialize sessions (Phase 5) if templates provided

    Args:
        plan_spec: Complete planning specification
        philosophy: Training philosophy defining constraints
        all_templates: Optional list of session templates for Phase 4 selection
        rag_bias: Optional RAG exclusion context
        philosophy_summary: Optional philosophy summary from RAG
        use_template_selection: Whether to use template selection (requires all_templates)
        conversation_id: Optional conversation ID for progress tracking

    Returns:
        List of WeekPlan objects, one per week in the plan

    Raises:
        PlanningInvariantError: If any week fails validation
    """
    weeks = []

    # STRUCTURE RESOLUTION (BEFORE any session logic)
    # TODO: These parameters need to be passed into compile_plan or computed here:
    #   - audience: str (from athlete profile/context)
    #   - days_to_race: int (calculated from plan_spec.end_date - today)
    # For now, using placeholders - these MUST be provided for full integration
    today = datetime.now(timezone.utc).date()
    days_to_race = (plan_spec.end_date - today).days if plan_spec.end_date else 100
    audience = "intermediate"  # TODO: Get from athlete profile/context
    phase = resolve_phase(days_to_race)

    planning_context = PlanningContext(
        philosophy_id=philosophy.id,
        race_type=plan_spec.race_type or "custom",
        audience=audience,
        phase=phase,
        days_to_race=days_to_race,
    )

    # Resolve and validate structure (frozen after this point)
    structure = resolve_structure(planning_context)
    validate_structure(structure)

    # Immutability guardrail: structure must not be mutated downstream
    structure_hash = hash(structure)

    # Phase 2: Week Skeleton Generation
    skeletons = generate_week_skeletons(plan_spec, philosophy)
    emit_planning_progress(
        phase="week_skeleton",
        status="completed",
        percent=20,
        message="Weekly structure created",
        summary={
            "weeks": len(skeletons),
            "days_per_week": plan_spec.days_per_week,
            "long_run_day": plan_spec.preferred_long_run_day,
        },
        conversation_id=conversation_id,
    )

    # Phase 3: Time Allocation (done per week in loop, emit after all weeks)
    total_allocation_complete = False

    for i, skeleton in enumerate(skeletons):
        allocation = allocate_week_time(
            skeleton,
            plan_spec.weekly_duration_targets_min[i],
            philosophy,
        )

        # Phase 3: Time Allocation completed (emit once after first week)
        if not total_allocation_complete:
            emit_planning_progress(
                phase="time_allocation",
                status="completed",
                percent=35,
                message="Training time allocated",
                summary={
                    "weekly_minutes": plan_spec.weekly_duration_targets_min,
                    "allocation_strategy": "deterministic_ratio",
                },
                conversation_id=conversation_id,
            )
            total_allocation_complete = True

        validate_compiled_week(
            skeleton,
            allocation,
            plan_spec.weekly_duration_targets_min[i],
            race_type=plan_spec.race_type or "default",
        )

        week_plan = assemble_week_plan(
            week_index=i,
            allocation=allocation,
            skeleton=skeleton,
            pace_min_per_mile=plan_spec.assumed_pace_min_per_mile,
        )

        # Phase 4: Template selection if templates provided
        # RAG RULE: RAG (rag_bias, philosophy_summary) is used here for:
        # - Excluding inappropriate templates (rag_bias)
        # - Providing explanatory context (philosophy_summary)
        # Structure (skeleton) is already fixed - RAG does not change it
        if use_template_selection and all_templates:
            week_plan = materialize_sessions_with_templates(
                week_plan=week_plan,
                skeleton_days=skeleton.days,
                allocation=allocation,
                philosophy=philosophy,
                race_type=plan_spec.race_type or "default",
                total_weeks=len(plan_spec.weekly_duration_targets_min),
                all_templates=all_templates,
                rag_bias=rag_bias,
                philosophy_summary=philosophy_summary,
                use_llm=True,
            )

            # Phase 5: Session materialization (after template selection)
            # Build template dictionary
            template_dict = {t.id: t for t in all_templates}

            # Materialize sessions
            concrete_sessions = materialize_week(
                week_plan=week_plan,
                templates=template_dict,
                pace_min_per_mile=plan_spec.assumed_pace_min_per_mile,
                generate_coach_text=False,  # Optional, can be enabled later
                philosophy_tags=list(philosophy.preferred_session_tags.keys()) if philosophy.preferred_session_tags else None,
            )

            # Validate materialized sessions
            validate_materialized_sessions(
                week_plan=week_plan,
                concrete_sessions=concrete_sessions,
                race_type=plan_spec.race_type or "default",
            )

            # Log metrics
            log_materialization_metrics(
                week_index=i,
                concrete_sessions=concrete_sessions,
                llm_text_used=False,  # Coach text disabled for now
            )

        weeks.append(week_plan)

    # Phase 4: Week Validation completed (after all weeks validated)
    emit_planning_progress(
        phase="week_validation",
        status="completed",
        percent=45,
        message="Weekly structure validated",
        summary={
            "weeks_validated": len(weeks),
            "invariants": "passed",
        },
        conversation_id=conversation_id,
    )

    # Phase 5: WeekPlan Assembly completed (after all weeks assembled)
    emit_planning_progress(
        phase="week_assembly",
        status="completed",
        percent=55,
        message="Weekly plans assembled",
        summary={
            "weeks": len(weeks),
            "sample_week": {
                "total_minutes": weeks[0].total_duration_min if weeks else 0,
                "sessions": len(weeks[0].sessions) if weeks else 0,
            },
        },
        conversation_id=conversation_id,
    )

    # Phase 6: Template Selection (Phase 4) - only if templates provided
    if use_template_selection and all_templates and weeks:
        # Template selection happens per week in loop above
        # Emit after all template selection is complete
        emit_planning_progress(
            phase="template_selection",
            status="completed",
            percent=70,
            message="Workouts selected",
            summary={
                "selection_method": "llm_bounded",
                "fallback_used": False,  # Could track this if needed
                "philosophy": philosophy.id if hasattr(philosophy, "id") else None,
            },
            conversation_id=conversation_id,
        )

        # Phase 7: Session Materialization (Phase 5) - already done per week in loop
        total_sessions = sum(len(w.sessions) for w in weeks)
        interval_count = sum(
            1 for w in weeks for s in w.sessions if s.session_type in {"interval", "tempo", "hills"}
        )
        emit_planning_progress(
            phase="materialization",
            status="completed",
            percent=85,
            message="Sessions fully defined",
            summary={
                "sessions_created": total_sessions,
                "interval_sessions": interval_count,
            },
            conversation_id=conversation_id,
        )

        # Phase 8: Materialization Validation - already done per week in loop
        emit_planning_progress(
            phase="materialization_validation",
            status="completed",
            percent=90,
            message="Final plan validated",
            summary={"status": "ready_for_execution"},
            conversation_id=conversation_id,
        )

    # Immutability guardrail: structure must not be mutated during compilation
    if hash(structure) != structure_hash:
        raise RuntimeError("Structure was mutated during compilation")

    return weeks
