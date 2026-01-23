"""Deterministic routing rules for intent × horizon → semantic tool.

This module provides the single source of truth for routing decisions.
No ambiguity - exactly one tool per intent/horizon combination.
"""

from typing import Literal

from loguru import logger

from app.tools.catalog import Horizon, Tier, get_tool_spec, validate_tool_horizon

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
    needs_approval: bool = False,
    query_type: str | None = None,
) -> str | None:
    """Route intent × horizon to semantic tool.

    Args:
        intent: User intent
        horizon: Time horizon
        has_proposal: Whether a proposal object exists
        needs_approval: Whether approval is required
        query_type: Optional query type hint (e.g., "schedule", "structure", "why")

    Returns:
        Tool name or None if no tool should be called

    Raises:
        ValueError: If intent/horizon combination is invalid
    """
    # Tier 1 - Informational
    if intent == "question":
        # General questions use no tool, or knowledge base if doc-based
        if query_type == "knowledge":
            return "query_coaching_knowledge"
        return None  # No tool, conversational response

    if intent == "general":
        # Chit-chat, meta, unsupported - no tools
        return None

    if intent == "explain":
        # Route based on what's being explained
        if query_type == "schedule":
            # "What do I have planned?" → get_planned_sessions
            return "get_planned_sessions"
        if query_type == "structure" and horizon in ("week", "season", "race"):
            # "Why is the plan structured this way?" → explain_plan_structure
            return "explain_plan_structure"
        if query_type == "why" or query_type == "rationale":
            # "Why did the plan change?" → generate_plan_rationale
            return "generate_plan_rationale"
        # For explain with horizon=None, check if it's a calendar/schedule query
        if horizon is None or horizon == "none":
            # If query mentions calendar/races/planned, use get_planned_sessions
            # Otherwise default to explain_training_state (will use week horizon)
            if query_type == "schedule":
                return "get_planned_sessions"
            # Default: explain training state (will default to week horizon in executor)
            return "explain_training_state"
        # Route based on horizon
        if horizon in ("today", "week", "season"):
            return "explain_training_state"
        if horizon in ("week", "season", "race"):
            # Could be structure explanation
            return "explain_plan_structure"
        # Default: explain training state
        return "explain_training_state"

    # Tier 2 - Decision
    if intent == "recommend":
        if horizon in ("next_session", "today"):
            return "recommend_next_session"
        if horizon in ("week", "season", "race"):
            # Recommend adjustments vs no change
            return "evaluate_plan_change"
        return None

    if intent == "propose":
        if not has_proposal:
            # No proposal object yet → clarify
            return None  # Will trigger clarify flow
        # Has proposal → preview it
        if horizon in ("day", "week", "season", "race"):
            return "preview_plan_change"
        return None

    if intent == "clarify":
        # No tool - just ask questions
        return None

    # Tier 3 - Mutation
    if intent == "plan":
        if horizon in ("race", "season"):
            return "plan"
        if horizon in ("week", "day"):
            # Week/day planning becomes modify with create-if-missing
            return "modify"
        return None

    if intent == "modify":
        if horizon in ("day", "week", "season", "race"):
            # Always creates proposal, does not apply
            return "modify"
        return None

    if intent == "adjust":
        if horizon in ("week", "season"):
            return "adjust_training_load"
        return None

    if intent == "log":
        if horizon == "today":
            return "log"
        return None

    if intent == "confirm":
        if horizon == "none":
            return "confirm"
        return None

    # Unknown intent
    return None


def route_with_safety_check(
    intent: Intent,
    horizon: Horizon,
    has_proposal: bool = False,
    needs_approval: bool = False,
    query_type: str | None = None,
    run_incoherence_check: bool = True,
) -> tuple[str | None, list[str]]:
    """Route with safety checks.

    Args:
        intent: User intent
        horizon: Time horizon
        has_proposal: Whether a proposal object exists
        needs_approval: Whether approval is required
        query_type: Optional query type hint
        run_incoherence_check: Whether to run incoherence detection

    Returns:
        Tuple of (tool_name, prerequisite_checks)
        - tool_name: Primary tool to execute
        - prerequisite_checks: List of tools to run first (e.g., detect_plan_incoherence)

    Raises:
        ValueError: If intent/horizon combination is invalid
    """
    tool_name = route(intent, horizon, has_proposal, needs_approval, query_type)

    prerequisite_checks: list[str] = []

    # Global safety check: detect incoherence before mutations
    if run_incoherence_check and tool_name and tool_name in ("modify", "plan", "adjust_training_load"):
        if horizon in ("today", "week", "season"):
            prerequisite_checks.append("detect_plan_incoherence")

    # Validate tool supports horizon (with fallback for None)
    if tool_name:
        spec = get_tool_spec(tool_name)
        if spec:
            # Check if tool supports "none" horizon
            if horizon is None or horizon == "none":
                if "none" not in spec.horizons:
                    # Tool doesn't support None - this is OK, executor will handle it
                    # Don't raise error, let the executor default the horizon
                    logger.debug(
                        "Routing: Tool doesn't support None horizon, executor will default",
                        tool=tool_name,
                        horizon=horizon,
                        supported_horizons=spec.horizons,
                    )
            else:
                # Validate specific horizon
                if not validate_tool_horizon(tool_name, horizon):  # type: ignore
                    raise ValueError(
                        f"Tool {tool_name} does not support horizon {horizon}. "
                        f"Supported horizons: {spec.horizons}"
                    )

    return tool_name, prerequisite_checks


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
