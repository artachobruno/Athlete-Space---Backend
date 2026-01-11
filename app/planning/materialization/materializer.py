"""Session Materializer - Main Orchestration.

Main orchestration unit for Phase 5 materialization.
Combines template expansion, distance derivation, and structure assembly.
"""

from app.planning.errors import PlanningInvariantError
from app.planning.library.session_template import SessionTemplate
from app.planning.materialization.expander import expand_template
from app.planning.materialization.models import ConcreteSession
from app.planning.materialization.pace import derive_distance_miles
from app.planning.output.models import MaterializedSession


def materialize_session(
    session: MaterializedSession,
    template: SessionTemplate,
    pace_min_per_mile: float,
) -> ConcreteSession:
    """Materialize a session from template.

    Flow:
    1. Expand template → structure
    2. Validate time totals
    3. Derive distance
    4. Attach structure
    5. Return ConcreteSession

    Args:
        session: MaterializedSession with locked duration and template ID
        template: SessionTemplate to expand
        pace_min_per_mile: Pace model - minutes per mile

    Returns:
        ConcreteSession with fully materialized structure

    Raises:
        ValueError: If template expansion fails
        PlanningInvariantError: If time validation fails
    """
    # Expand template into structure
    expanded = expand_template(template, session.duration_minutes)

    # Validate time totals (basic check - expander should handle allocation correctly)
    total_allocated = 0
    if expanded.warmup_minutes:
        total_allocated += expanded.warmup_minutes
    if expanded.cooldown_minutes:
        total_allocated += expanded.cooldown_minutes

    # Calculate interval time if present, otherwise use main set minutes
    if expanded.intervals:
        for interval in expanded.intervals:
            interval_time = interval.reps * (interval.work_min + interval.rest_min)
            total_allocated += interval_time
    else:
        # If no intervals, assume main set is continuous
        total_allocated += expanded.main_set_minutes

    # Validate total doesn't exceed session duration (allow small rounding tolerance)
    # Note: total_allocated might be slightly less than duration_minutes if template
    # doesn't use full time, which is acceptable
    if total_allocated > session.duration_minutes + 1:
        raise PlanningInvariantError(
            "TIME_ALLOCATION_EXCEEDS_DURATION",
            [
                f"Allocated time {total_allocated}min exceeds session duration {session.duration_minutes}min",
                f"Template: {template.id}",
            ],
        )

    # Derive distance
    distance_miles = derive_distance_miles(session.duration_minutes, pace_min_per_mile)

    # Build ConcreteSession
    return ConcreteSession(
        day=session.day,
        session_template_id=session.session_template_id,
        session_type=session.session_type,
        duration_minutes=session.duration_minutes,
        distance_miles=distance_miles,
        warmup_minutes=expanded.warmup_minutes,
        cooldown_minutes=expanded.cooldown_minutes,
        intervals=expanded.intervals,
        instructions=None,  # Will be filled by coach_text module if needed
    )
