"""Canonical tool catalog - single source of truth for semantic tools.

This module defines all semantic tools available to the orchestrator.
Implementation details (MCP, DB, executor) are hidden behind adapters.
"""

from dataclasses import dataclass
from typing import Literal

Horizon = Literal["today", "next_session", "week", "season", "race", "none"]
Tier = Literal["info", "decision", "mutation"]
Approval = Literal["never", "optional", "required"]


@dataclass(frozen=True)
class ToolSpec:
    """Specification for a semantic tool."""

    name: str
    tier: Tier
    horizons: list[Horizon]
    approval: Approval
    description: str
    schema_key: str | None = None  # Reference to schema definition


# Canonical tool catalog - single source of truth
CANONICAL_TOOLS: dict[str, ToolSpec] = {
    # Tier 1 - Informational
    "get_planned_sessions": ToolSpec(
        name="get_planned_sessions",
        tier="info",
        horizons=["today", "week", "season"],
        approval="never",
        description="Read-only schedule retrieval",
    ),
    "get_recent_activities": ToolSpec(
        name="get_recent_activities",
        tier="info",
        horizons=["today", "week"],
        approval="never",
        description="Execution data retrieval",
    ),
    "explain_training_state": ToolSpec(
        name="explain_training_state",
        tier="info",
        horizons=["today", "week", "season"],
        approval="never",
        description="Metrics and risk explanation",
    ),
    "explain_plan_structure": ToolSpec(
        name="explain_plan_structure",
        tier="info",
        horizons=["week", "season", "race"],
        approval="never",
        description="Plan intent and rationale explanation",
    ),
    "generate_plan_rationale": ToolSpec(
        name="generate_plan_rationale",
        tier="info",
        horizons=["week", "season", "race"],
        approval="never",
        description="LLM-generated plan rationale",
    ),
    "query_coaching_knowledge": ToolSpec(
        name="query_coaching_knowledge",
        tier="info",
        horizons=["none"],
        approval="never",
        description="RAG/FAQ knowledge queries",
    ),
    # Tier 2 - Decision (no mutation)
    "recommend_next_session": ToolSpec(
        name="recommend_next_session",
        tier="decision",
        horizons=["next_session", "today"],
        approval="never",
        description="Suggests next workout",
    ),
    "evaluate_plan_change": ToolSpec(
        name="evaluate_plan_change",
        tier="decision",
        horizons=["week", "season", "race"],
        approval="never",
        description="Evaluates whether plan changes are needed",
    ),
    "preview_plan_change": ToolSpec(
        name="preview_plan_change",
        tier="decision",
        horizons=["today", "week", "season", "race"],
        approval="never",
        description="Shows diff preview before confirmation",
    ),
    "detect_plan_incoherence": ToolSpec(
        name="detect_plan_incoherence",
        tier="decision",
        horizons=["today", "week", "season"],
        approval="never",
        description="Detects contradictions in plan structure",
    ),
    "recommend_no_change": ToolSpec(
        name="recommend_no_change",
        tier="decision",
        horizons=["week", "season"],
        approval="never",
        description="Explicit no-change recommendation",
    ),
    # Tier 3 - Mutation (must go through approval)
    "plan": ToolSpec(
        name="plan",
        tier="mutation",
        horizons=["today", "week", "season", "race"],
        approval="optional",
        description="Initial plan creation (week/today when no plan exists)",
    ),
    "modify": ToolSpec(
        name="modify",
        tier="mutation",
        horizons=["today", "week", "season", "race"],
        approval="required",
        description="Structured plan modifications",
    ),
    "adjust_training_load": ToolSpec(
        name="adjust_training_load",
        tier="mutation",
        horizons=["week", "season"],
        approval="required",
        description="Training load parameter adjustments",
    ),
    "add_workout": ToolSpec(
        name="add_workout",
        tier="mutation",
        horizons=["today", "week"],
        approval="required",
        description="Calendar workout addition",
    ),
    "log": ToolSpec(
        name="log",
        tier="mutation",
        horizons=["today"],
        approval="never",
        description="Record activity or feedback",
    ),
    "confirm": ToolSpec(
        name="confirm",
        tier="mutation",
        horizons=["none"],
        approval="required",
        description="Applies a proposed change",
    ),
}


def get_tool_spec(tool_name: str) -> ToolSpec | None:
    """Get tool specification by name."""
    return CANONICAL_TOOLS.get(tool_name)


def get_tools_by_tier(tier: Tier) -> list[ToolSpec]:
    """Get all tools in a specific tier."""
    return [spec for spec in CANONICAL_TOOLS.values() if spec.tier == tier]


def get_tools_by_horizon(horizon: Horizon) -> list[ToolSpec]:
    """Get all tools that support a specific horizon."""
    return [spec for spec in CANONICAL_TOOLS.values() if horizon in spec.horizons]


def is_semantic_tool(tool_name: str) -> bool:
    """Check if a tool name is a semantic tool (exists in catalog)."""
    return tool_name in CANONICAL_TOOLS


def validate_tool_horizon(tool_name: str, horizon: Horizon) -> bool:
    """Validate that a tool supports the given horizon."""
    spec = get_tool_spec(tool_name)
    if not spec:
        return False
    return horizon in spec.horizons


def is_mutation_tool(tool_name: str) -> bool:
    """Check if a tool is a mutation tool (tier 3)."""
    spec = get_tool_spec(tool_name)
    return spec is not None and spec.tier == "mutation"
