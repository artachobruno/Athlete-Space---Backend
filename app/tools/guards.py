"""Guards and invariants for tool execution.

Hard rules that must be enforced, not conventions.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from app.db.models import PlanEvaluation
from app.db.session import get_session
from app.tools.catalog import get_tool_spec, is_mutation_tool
from app.tools.registry import SEMANTIC_TOOL_REGISTRY
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change


class EvaluationRequiredError(Exception):
    """Raised when mutation is attempted without recent evaluation.

    Retained for backward compatibility; guards no longer raise this
    for evaluation failure (evaluation is non-blocking).
    """

    pass


def require_recent_evaluation(
    user_id: str,
    athlete_id: int,
    horizon: Literal["week", "season", "race"],
    tool_name: str,
    today: date | None = None,
    action: Literal["NO_ACTION", "EXECUTE"] | None = None,
) -> None:
    """Require recent evaluation before mutation (PROPOSE/ADJUST only).

    Invariant: Evaluation is allowed ONLY when action in {NO_ACTION, PROPOSE, ADJUST}.
    NEVER during EXECUTE. Guards are not evaluators; we skip entirely for EXECUTE.

    Evaluation failure is non-blocking: log and continue. Never block execution.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        horizon: Time horizon
        tool_name: Tool name being executed
        today: Current date (defaults to today)
        action: Orchestrator action (EXECUTE, NO_ACTION). When EXECUTE, skip guard.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    if action == "EXECUTE":
        logger.debug(
            "Skipping evaluation guard for EXECUTE action",
            tool=tool_name,
            horizon=horizon,
        )
        return

    # Check if tool is a mutation tool
    spec = get_tool_spec(tool_name)
    if not spec or not is_mutation_tool(tool_name):
        return

    # Check for recent evaluation in plan_evaluations table
    recent_evaluation = None
    try:
        with get_session() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            recent_evaluation = session.execute(
                select(PlanEvaluation)
                .where(
                    PlanEvaluation.athlete_id == athlete_id,
                    PlanEvaluation.horizon == horizon,
                    PlanEvaluation.created_at >= cutoff,
                )
                .order_by(PlanEvaluation.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
    except ProgrammingError as e:
        if "does not exist" in str(e).lower() or "undefinedtable" in str(e).lower():
            logger.debug(
                "plan_evaluations table does not exist - treating as no recent evaluation",
                tool=tool_name,
                horizon=horizon,
            )
            recent_evaluation = None
        else:
            raise

    if not recent_evaluation:
        logger.warning(
            "Mutation attempted without recent evaluation - running evaluation (non-blocking)",
            tool=tool_name,
            horizon=horizon,
            user_id=user_id,
            athlete_id=athlete_id,
        )
        try:
            evaluate_plan_change(
                user_id=user_id,
                athlete_id=athlete_id,
                horizon=horizon,
                today=today,
            )
            logger.info(
                "Evaluation completed - mutation can proceed",
                tool=tool_name,
                horizon=horizon,
            )
        except Exception as e:
            logger.warning(
                "Evaluation failed (non-blocking) - continuing with mutation",
                tool=tool_name,
                horizon=horizon,
                error=str(e),
            )
        return

    logger.debug(
        "Recent evaluation found - mutation can proceed",
        tool=tool_name,
        horizon=horizon,
        evaluation_id=recent_evaluation.id,
        evaluation_date=recent_evaluation.created_at,
        decision=recent_evaluation.decision,
    )


def validate_semantic_tool_only(tool_name: str) -> None:
    """Validate that only semantic tools are used.

    Hard rule: Orchestrator must only use tools from semantic catalog.

    Args:
        tool_name: Tool name to validate

    Raises:
        ValueError: If tool is not a semantic tool
    """
    if not SEMANTIC_TOOL_REGISTRY.validate_tool_name(tool_name):
        raise ValueError(
            f"Tool '{tool_name}' is not a semantic tool. "
            f"Only tools from the semantic catalog are allowed. "
            f"Available tools: {SEMANTIC_TOOL_REGISTRY.list_tools()}"
        )
