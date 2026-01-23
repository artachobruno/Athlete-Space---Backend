"""Executor adapter for semantic tools.

This adapter wraps executor tool calls. Semantic tools should never
directly import or call executor code.
"""

from typing import Any

from loguru import logger

# Import executor functions here - these are implementation details
from app.coach.tools.add_workout import add_workout as executor_add_workout
from app.coach.tools.adjust_load import adjust_training_load as executor_adjust_load
from app.coach.tools.explain_state import explain_training_state as executor_explain_state
from app.coach.tools.next_session import recommend_next_session as executor_recommend_next_session
from app.coach.tools.plan_race import plan_race_build as executor_plan_race
from app.coach.tools.plan_season import plan_season as executor_plan_season
from app.coach.tools.plan_week import plan_week as executor_plan_week


async def plan_week(_ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
    """Plan week via executor adapter."""
    logger.debug("Executor adapter: plan_week", **args)
    # Executor tools are sync, so we call them directly
    result = executor_plan_week(**args)
    return {"message": result, "success": True}


async def plan_season(_ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
    """Plan season via executor adapter."""
    logger.debug("Executor adapter: plan_season", **args)
    result = executor_plan_season(**args)
    return {"message": result, "success": True}


async def plan_race(_ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Plan race via executor adapter."""
    logger.debug("Executor adapter: plan_race", **args)
    result = await executor_plan_race(**args)
    return {"message": result, "success": True}


async def recommend_next_session(_ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
    """Recommend next session via executor adapter."""
    logger.debug("Executor adapter: recommend_next_session", **args)
    result = executor_recommend_next_session(**args)
    return {"message": result, "success": True}


async def explain_training_state(_ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
    """Explain training state via executor adapter."""
    logger.debug("Executor adapter: explain_training_state", **args)
    result = executor_explain_state(**args)
    return {"message": result, "success": True}


async def adjust_training_load(_ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
    """Adjust training load via executor adapter."""
    logger.debug("Executor adapter: adjust_training_load", **args)
    result = executor_adjust_load(**args)
    return {"message": result, "success": True}


async def add_workout(_ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
    """Add workout via executor adapter."""
    logger.debug("Executor adapter: add_workout", **args)
    result = executor_add_workout(**args)
    return {"message": result, "success": True}
