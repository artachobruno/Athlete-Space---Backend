"""Phase 2 Integration Function.

This module orchestrates the complete Phase 2 compilation pipeline:
PlanSpec → WeekSkeletons → Time Allocation → Validation → WeekPlans → Template Selection (Phase 4) → Session Materialization (Phase 5)
"""

from app.planning.materialization.metrics import log_materialization_metrics

from app.planning.compiler.assemble_week import assemble_week_plan
from app.planning.compiler.skeleton_generator import generate_week_skeletons
from app.planning.compiler.time_allocator import allocate_week_time
from app.planning.compiler.validate_compiled_week import validate_compiled_week
from app.planning.library.philosophy import TrainingPhilosophy
from app.planning.library.session_template import SessionTemplate
from app.planning.materialization.materialize_week import materialize_week
from app.planning.materialization.validate import validate_materialized_sessions
from app.planning.output.models import WeekPlan
from app.planning.schemas.plan_spec import PlanSpec
from app.planning.selection.integration import materialize_sessions_with_templates


def compile_plan(
    plan_spec: PlanSpec,
    philosophy: TrainingPhilosophy,
    *,
    all_templates: list[SessionTemplate] | None = None,
    rag_bias: dict[str, list[str]] | None = None,
    philosophy_summary: str | None = None,
    use_template_selection: bool = True,
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

    Returns:
        List of WeekPlan objects, one per week in the plan

    Raises:
        PlanningInvariantError: If any week fails validation
    """
    weeks = []

    skeletons = generate_week_skeletons(plan_spec, philosophy)

    for i, skeleton in enumerate(skeletons):
        allocation = allocate_week_time(
            skeleton,
            plan_spec.weekly_duration_targets_min[i],
            philosophy,
        )

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

    return weeks
