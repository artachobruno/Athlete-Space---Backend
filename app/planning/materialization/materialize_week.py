"""Full Week Materialization.

Materializes all sessions in a WeekPlan into ConcreteSessions.
Preserves order and skips rest days.
"""

from loguru import logger

from app.planning.library.session_template import SessionTemplate
from app.planning.materialization.coach_text import generate_coach_text_sync
from app.planning.materialization.materializer import materialize_session
from app.planning.materialization.models import ConcreteSession
from app.planning.output.models import MaterializedSession, WeekPlan


def materialize_week(
    week_plan: WeekPlan,
    templates: dict[str, SessionTemplate],
    pace_min_per_mile: float,
    generate_coach_text: bool = False,
    philosophy_tags: list[str] | None = None,
) -> list[ConcreteSession]:
    """Materialize all sessions in a week.

    Rules:
    - Rest days skipped
    - Order preserved
    - All sessions validated post-materialization

    Args:
        week_plan: WeekPlan with MaterializedSessions (template IDs assigned)
        templates: Dictionary mapping template IDs to SessionTemplate objects
        pace_min_per_mile: Pace model - minutes per mile
        generate_coach_text: Whether to generate optional coach text via LLM
        philosophy_tags: Optional philosophy tags for coach text context

    Returns:
        List of ConcreteSession objects (rest days excluded)

    Raises:
        ValueError: If template not found for a session
        PlanningInvariantError: If materialization fails
    """
    concrete_sessions: list[ConcreteSession] = []

    logger.debug(
        "materialize_week: Starting week materialization",
        week_index=week_plan.week_index,
        sessions_count=len(week_plan.sessions),
    )

    for session in week_plan.sessions:
        # Skip rest days
        if session.session_type == "rest":
            continue

        # Skip unassigned templates
        if session.session_template_id == "UNASSIGNED":
            logger.warning(
                "materialize_week: Skipping session with unassigned template",
                day=session.day,
                session_type=session.session_type,
            )
            continue

        # Get template
        template = templates.get(session.session_template_id)
        if not template:
            raise ValueError(
                f"Template not found for session {session.day}: {session.session_template_id}"
            )

        # Materialize session
        concrete = materialize_session(session, template, pace_min_per_mile)

        # Generate optional coach text
        if generate_coach_text:
            coach_text = generate_coach_text_sync(concrete, template, philosophy_tags)
            if coach_text:
                # Create new ConcreteSession with instructions
                concrete = ConcreteSession(
                    day=concrete.day,
                    session_template_id=concrete.session_template_id,
                    session_type=concrete.session_type,
                    duration_minutes=concrete.duration_minutes,
                    distance_miles=concrete.distance_miles,
                    warmup_minutes=concrete.warmup_minutes,
                    cooldown_minutes=concrete.cooldown_minutes,
                    intervals=concrete.intervals,
                    instructions=coach_text,
                )

        concrete_sessions.append(concrete)

    logger.debug(
        "materialize_week: Week materialization completed",
        week_index=week_plan.week_index,
        concrete_sessions_count=len(concrete_sessions),
    )

    return concrete_sessions
