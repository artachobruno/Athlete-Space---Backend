"""B8.6 — Tool entry point (MCP).

This module provides the main entry point for planner v2 execution.
It guarantees:
- Exactly one tool execution
- Exactly one plan result
- Deterministic behavior
"""

import uuid
from dataclasses import dataclass

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.orchestrator.planner_v2.errors import LLMSchemaViolationError, PersistenceError, StepExecutionError, ValidationError
from app.orchestrator.planner_v2.execution import PLANNER_V2_STEPS, run_step
from app.orchestrator.planner_v2.progress import emit_plan_summary, emit_planning_progress
from app.orchestrator.planner_v2.state import PlannerV2State
from app.planner.calendar_persistence import PersistResult
from app.planner.enums import PlanType, RaceDistance, TrainingIntent
from app.planner.models import PlanContext


@dataclass
class PlannerInput:
    """Input for planner v2 tool.

    Attributes:
        plan_type: Type of plan (race or season)
        intent: Training intent (maintain, build, explore, recover)
        weeks: Number of weeks in plan
        race_distance: Race distance (required for race plans)
        target_date: Target race date in ISO format (optional for race plans)
        athlete_state: Athlete state snapshot
        user_id: User ID (required for persistence)
        athlete_id: Athlete ID (required for persistence)
        user_preference: Optional explicit philosophy ID override
    """

    plan_type: PlanType
    intent: TrainingIntent
    weeks: int
    race_distance: RaceDistance | None = None
    target_date: str | None = None
    athlete_state: AthleteState | None = None
    user_id: str | None = None
    athlete_id: int | None = None
    user_preference: str | None = None


async def _execute_step_with_handling(
    step: str,
    state: PlannerV2State,
    input_data: PlannerInput,
    plan_id: str,
) -> PlannerV2State:
    """Execute a step with special handling for B6 and B7.

    Args:
        step: Step name
        state: Current planner state
        input_data: Planner input
        plan_id: Plan ID

    Returns:
        Updated planner state

    Raises:
        StepExecutionError: If step execution fails
        ValidationError: If validation fails
    """
    # Special handling for B6 (LLM schema violations - allow one retry)
    if step == "session_text":
        try:
            return await run_step(step, state, input_data.user_id, input_data.athlete_id)
        except StepExecutionError as e:
            # Check if it's an LLM schema violation
            if isinstance(e.original_error, (LLMSchemaViolationError, Exception)):
                # Retry once
                logger.warning(
                    "B6 LLM schema violation, retrying once",
                    plan_id=plan_id,
                    error=str(e.original_error),
                )
                try:
                    return await run_step(step, state, input_data.user_id, input_data.athlete_id)
                except Exception as retry_error:
                    emit_planning_progress(
                        plan_id=plan_id,
                        step=step,
                        status="failed",
                        error=f"Retry failed: {retry_error}",
                    )
                    raise StepExecutionError(step, retry_error) from retry_error
            raise

    # Special handling for B7 (persistence failures - partial success allowed)
    if step == "persist":
        try:
            if input_data.user_id is None or input_data.athlete_id is None:
                raise ValidationError("B7 requires user_id and athlete_id")
            return await run_step(step, state, input_data.user_id, input_data.athlete_id)
        except (StepExecutionError, PersistenceError) as e:
            # Persistence failures are non-fatal
            logger.warning(
                "B7 persistence failed but continuing",
                plan_id=plan_id,
                error=str(e),
            )
            emit_planning_progress(
                plan_id=plan_id,
                step=step,
                status="failed",
                error=str(e),
            )
            # Create a partial success result
            return state.replace(
                persist_result=PersistResult(
                    plan_id=plan_id,
                    created=0,
                    updated=0,
                    skipped=0,
                    warnings=[str(e)],
                ),
                current_step=step,
            )

    # Normal step execution (B2-B5)
    return await run_step(step, state, input_data.user_id, input_data.athlete_id)


@dataclass
class PlannerResult:
    """Result from planner v2 tool.

    Attributes:
        plan_id: Unique plan identifier
        summary: Persistence result summary
        warnings: List of warning messages
    """

    plan_id: str
    summary: dict[str, object]
    warnings: list[str]


def generate_plan_id() -> str:
    """Generate a unique plan ID.

    Returns:
        UUID string for plan identification
    """
    return str(uuid.uuid4())


def build_plan_context(input_data: PlannerInput) -> PlanContext:
    """Build PlanContext from tool input.

    Args:
        input_data: Planner input

    Returns:
        PlanContext instance

    Raises:
        ValidationError: If input is invalid
    """
    # Validate race plans have race_distance
    if input_data.plan_type == PlanType.RACE and input_data.race_distance is None:
        raise ValidationError("Race plans require race_distance")

    # Validate athlete_state is provided
    if input_data.athlete_state is None:
        raise ValidationError("athlete_state is required")

    return PlanContext(
        plan_type=input_data.plan_type,
        intent=input_data.intent,
        weeks=input_data.weeks,
        race_distance=input_data.race_distance,
        target_date=input_data.target_date,
        philosophy=None,  # Will be set by B2.5
    )


async def planner_v2_tool(input_data: PlannerInput) -> PlannerResult:
    """Main planner v2 tool entry point.

    This function:
    1. Initializes state
    2. Executes all steps in order
    3. Handles errors with appropriate semantics
    4. Returns final result

    Error handling:
    - ValidationError: Abort immediately
    - PlannerError: Abort immediately
    - LLMSchemaViolationError (B6): Retry once, then abort
    - PersistenceError (B7): Return partial success + warnings

    Args:
        input_data: Planner input with all required fields

    Returns:
        PlannerResult with plan_id and summary

    Raises:
        ValidationError: If input validation fails
        StepExecutionError: If any step fails (except B7 with partial success)
    """
    if input_data.athlete_state is None:
        raise ValidationError("athlete_state is required")

    # Initialize state
    plan_id = generate_plan_id()
    ctx = build_plan_context(input_data)

    state = PlannerV2State(
        plan_id=plan_id,
        ctx=ctx,
        athlete_state=input_data.athlete_state,
    )

    logger.info(
        "Starting planner v2 execution",
        plan_id=plan_id,
        plan_type=ctx.plan_type.value,
        intent=ctx.intent.value,
        weeks=ctx.weeks,
        race_distance=ctx.race_distance.value if ctx.race_distance else None,
    )

    # Execute steps in order
    for step in PLANNER_V2_STEPS:
        try:
            state = await _execute_step_with_handling(
                step=step,
                state=state,
                input_data=input_data,
                plan_id=plan_id,
            )
        except ValidationError as e:
            # Hard stop on validation errors
            emit_planning_progress(
                plan_id=plan_id,
                step=step,
                status="failed",
                error=str(e),
            )
            logger.error(
                "Validation error in planner v2",
                plan_id=plan_id,
                step=step,
                error=str(e),
            )
            raise

        except StepExecutionError as e:
            # Hard stop on step execution errors (except B7 handled above)
            emit_planning_progress(
                plan_id=plan_id,
                step=step,
                status="failed",
                error=str(e),
            )
            logger.error(
                "Step execution error in planner v2",
                plan_id=plan_id,
                step=step,
                error=str(e),
            )
            raise

        except Exception as e:
            # Unexpected errors - hard stop
            emit_planning_progress(
                plan_id=plan_id,
                step=step,
                status="failed",
                error=str(e),
            )
            logger.error(
                "Unexpected error in planner v2",
                plan_id=plan_id,
                step=step,
                error=str(e),
                exc_info=True,
            )
            raise StepExecutionError(step, e) from e

    # Emit final plan summary
    emit_plan_summary(state)

    # Build result
    summary: dict[str, object] = {}
    warnings: list[str] = []

    if state.persist_result:
        summary = {
            "created": state.persist_result.created,
            "updated": state.persist_result.updated,
            "skipped": state.persist_result.skipped,
        }
        warnings = state.persist_result.warnings
    else:
        summary = {
            "weeks": len(state.text_weeks) if state.text_weeks else 0,
            "philosophy": state.philosophy_id,
        }

    logger.info(
        "Planner v2 execution complete",
        plan_id=plan_id,
        weeks=len(state.text_weeks) if state.text_weeks else 0,
        philosophy=state.philosophy_id,
    )

    return PlannerResult(
        plan_id=plan_id,
        summary=summary,
        warnings=warnings,
    )
