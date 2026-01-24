"""Deterministic routing rules for intent x horizon → semantic tool.

This module provides the single source of truth for routing decisions.
No ambiguity - exactly one tool per intent/horizon combination.
Routing decides CREATE vs MODIFY vs PREVIEW.
"""

from typing import Literal

from loguru import logger

from app.coach.routing.route import has_existing_plan
from app.coach.routing.types import RoutedTool
from app.tools.catalog import Horizon, get_tool_spec, validate_tool_horizon

Intent = Literal[
    "question",
    "general",
    "explain",
    "recommend",
    "propose",
    "clarify",
    "plan",
    "modify",
    "adjust",
    "log",
    "confirm",
]


def route(
    intent: Intent,
    horizon: Horizon,
    has_proposal: bool = False,
    needs_approval: bool = False,  # noqa: ARG001
    query_type: str | None = None,
    athlete_id: int | None = None,
) -> RoutedTool | None:
    """Route intent x horizon to semantic tool.

    Returns RoutedTool with name and mode (CREATE/MODIFY/PREVIEW). Mode is set
    only for plan/create, modify, and preview; otherwise None.

    Args:
        intent: User intent
        horizon: Time horizon
        has_proposal: Whether a proposal object exists
        needs_approval: Whether approval is required (unused, API compat)
        query_type: Optional query type hint (e.g., "schedule", "structure", "why")
        athlete_id: Athlete ID for plan existence check (plan + week/today only)

    Returns:
        RoutedTool or None if no tool should be called
    """
    # Tier 1 - Informational
    if intent == "question":
        if query_type == "knowledge":
            return RoutedTool(name="query_coaching_knowledge", mode=None)
        return None

    if intent == "general":
        return None

    if intent == "explain":
        if query_type == "schedule":
            return RoutedTool(name="get_planned_sessions", mode=None)
        if query_type == "structure" and horizon in {"week", "season", "race"}:
            return RoutedTool(name="explain_plan_structure", mode=None)
        if query_type in {"why", "rationale"}:
            return RoutedTool(name="generate_plan_rationale", mode=None)
        if horizon is None or horizon == "none":
            if query_type == "schedule":
                return RoutedTool(name="get_planned_sessions", mode=None)
            return RoutedTool(name="explain_training_state", mode=None)
        if horizon in {"today", "week", "season"}:
            return RoutedTool(name="explain_training_state", mode=None)
        if horizon in {"week", "season", "race"}:
            return RoutedTool(name="explain_plan_structure", mode=None)
        return RoutedTool(name="explain_training_state", mode=None)

    # Tier 2 - Decision
    if intent == "recommend":
        if horizon in {"next_session", "today"}:
            return RoutedTool(name="recommend_next_session", mode=None)
        if horizon in {"week", "season", "race"}:
            return RoutedTool(name="evaluate_plan_change", mode=None)
        return None

    if intent == "propose":
        if not has_proposal:
            return None
        if horizon in {"today", "week", "season", "race"}:
            return RoutedTool(name="preview", mode="PREVIEW")
        return None

    if intent == "clarify":
        return None

    # Tier 3 - Mutation: CREATE vs MODIFY by plan existence only
    if intent == "plan":
        if horizon in {"race", "season"}:
            return RoutedTool(name="plan", mode=None)
        if horizon in {"week", "today"}:
            if athlete_id is None:
                return RoutedTool(name="plan", mode="CREATE")
            if has_existing_plan(athlete_id, horizon):
                return RoutedTool(name="modify", mode="MODIFY")
            return RoutedTool(name="plan", mode="CREATE")
        return None

    if intent == "modify":
        if horizon in {"today", "week", "season", "race"}:
            return RoutedTool(name="modify", mode="MODIFY")
        return None

    if intent == "adjust":
        if horizon in {"week", "season"}:
            return RoutedTool(name="adjust_training_load", mode=None)
        return None

    if intent == "log":
        if horizon == "today":
            return RoutedTool(name="log", mode=None)
        return None

    if intent == "confirm":
        if horizon == "none":
            return RoutedTool(name="confirm", mode=None)
        return None

    return None


def route_with_safety_check(
    intent: Intent,
    horizon: Horizon,
    has_proposal: bool = False,
    needs_approval: bool = False,
    query_type: str | None = None,
    run_incoherence_check: bool = True,
    athlete_id: int | None = None,
) -> tuple[RoutedTool | None, list[str]]:
    """Route with safety checks.

    Returns (RoutedTool | None, prerequisite_checks). Use .name for tool execution.
    """
    rt = route(
        intent, horizon, has_proposal, needs_approval, query_type,
        athlete_id=athlete_id,
    )

    prerequisite_checks: list[str] = []
    tool_name = rt.name if rt else None

    if (
        run_incoherence_check
        and tool_name
        and tool_name in {"modify", "plan", "adjust_training_load"}
        and horizon in {"today", "week", "season"}
    ):
        prerequisite_checks.append("detect_plan_incoherence")

    if rt:
        spec = get_tool_spec(rt.name)
        if spec:
            if horizon is None or horizon == "none":
                if "none" not in spec.horizons:
                    logger.debug(
                        "Routing: Tool doesn't support None horizon, executor will default",
                        tool=rt.name,
                        horizon=horizon,
                        supported_horizons=spec.horizons,
                    )
            elif not validate_tool_horizon(rt.name, horizon):  # type: ignore
                raise ValueError(
                    f"Tool {rt.name} does not support horizon {horizon}. "
                    f"Supported horizons: {spec.horizons}"
                )

    return rt, prerequisite_checks


def get_required_approval(tool_name: str) -> Literal["never", "optional", "required"]:
    """Get approval requirement for a tool."""
    spec = get_tool_spec(tool_name)
    if not spec:
        return "never"
    return spec.approval


def is_mutation_tool(tool_name: str) -> bool:
    """Check if a tool is a mutation tool."""
    spec = get_tool_spec(tool_name)
    return spec is not None and spec.tier == "mutation"


def is_read_only_tool(tool_name: str) -> bool:
    """Check if a tool is read-only (info tier)."""
    spec = get_tool_spec(tool_name)
    return spec is not None and spec.tier == "info"
