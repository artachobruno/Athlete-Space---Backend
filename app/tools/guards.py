"""Guards and invariants for tool execution.

Hard rules that must be enforced, not conventions.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import select

from app.db.models import PlanEvaluation
from app.db.session import get_session
from app.tools.catalog import get_tool_spec, is_mutation_tool
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change


class EvaluationRequiredError(Exception):
    """Raised when mutation is attempted without recent evaluation."""

    pass


async def require_recent_evaluation(
    user_id: str,
    athlete_id: int,
    horizon: Literal["week", "season", "race"],
    tool_name: str,
    today: date | None = None,
) -> None:
    """Require recent evaluation before mutation.

    Hard invariant: No plan mutation may occur unless evaluation has been run first.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        horizon: Time horizon
        tool_name: Tool name being executed
        today: Current date (defaults to today)

    Raises:
        EvaluationRequiredError: If evaluation is missing or stale
    """
    if today is None:
        today = date.today()

    # Check if tool is a mutation tool
    spec = get_tool_spec(tool_name)
    if not spec or not is_mutation_tool(tool_name):
        # Not a mutation tool, no evaluation required
        return

    # Check for recent evaluation in plan_evaluations table
    with get_session() as session:
        # Look for recent evaluation (within last 7 days for same horizon and athlete)
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

        if not recent_evaluation:
            # No recent evaluation - force one
            logger.warning(
                "Mutation attempted without recent evaluation - forcing evaluation",
                tool=tool_name,
                horizon=horizon,
                user_id=user_id,
                athlete_id=athlete_id,
            )
            # Run evaluation now (this will store it automatically)
            try:
                await evaluate_plan_change(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    horizon=horizon,
                    today=today,
                )
                logger.info(
                    "Evaluation completed and stored - mutation can proceed",
                    tool=tool_name,
                    horizon=horizon,
                )
            except Exception as e:
                logger.exception(
                    "Failed to run required evaluation",
                    tool=tool_name,
                    horizon=horizon,
                    error=str(e),
                )
                raise EvaluationRequiredError(
                    f"Mutation requires evaluation but evaluation failed: {e}"
                ) from e
        else:
            # Recent evaluation found - check if it's still valid
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
    from app.tools.registry import SEMANTIC_TOOL_REGISTRY

    if not SEMANTIC_TOOL_REGISTRY.validate_tool_name(tool_name):
        raise ValueError(
            f"Tool '{tool_name}' is not a semantic tool. "
            f"Only tools from the semantic catalog are allowed. "
            f"Available tools: {SEMANTIC_TOOL_REGISTRY.list_tools()}"
        )
