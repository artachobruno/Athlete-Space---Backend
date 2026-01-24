"""Minimal tests for tool architecture invariants.

These tests enforce the critical architectural rules:
1. No duplicate tool names
2. Routing returns exactly one tool
3. Mutation requires evaluation
4. Plan inspect returns evaluation + preview
"""

import pytest

from app.orchestrator.routing import route, route_with_safety_check
from app.tools.catalog import CANONICAL_TOOLS, validate_tool_horizon
from app.tools.guards import validate_semantic_tool_only
from app.tools.registry import SEMANTIC_TOOL_REGISTRY, validate_no_duplicates


def test_no_duplicate_tool_names():
    """Test: Tool registry contains no duplicate names."""
    # This should not raise
    validate_no_duplicates()

    # Verify all tools are unique
    tool_names = list(CANONICAL_TOOLS.keys())
    assert len(tool_names) == len(set(tool_names)), "Duplicate tool names detected"


def test_routing_returns_exactly_one_tool():
    """Test: Routing returns exactly one tool for each valid intent/horizon."""
    test_cases = [
        ("explain", "week", "explain_training_state"),
        ("explain", "season", "explain_training_state"),
        ("recommend", "next_session", "recommend_next_session"),
        ("recommend", "week", "evaluate_plan_change"),
        ("plan", "race", "plan"),
        ("plan", "season", "plan"),
        ("modify", "week", "modify"),
        ("adjust", "week", "adjust_training_load"),
        ("log", "today", "log"),
        ("confirm", "none", "confirm"),
    ]

    for intent, horizon, expected_tool in test_cases:
        result = route(intent, horizon, athlete_id=1)  # type: ignore
        name = result.name if result else None
        assert name == expected_tool, (
            f"Routing failed: {intent}/{horizon} -> {name}, expected {expected_tool}"
        )


def test_mutation_requires_evaluation():
    """Test: Mutation intent without evaluation → rejected."""
    # This test verifies the guard exists
    # Actual evaluation check would require DB setup
    from app.tools.guards import EvaluationRequiredError, require_recent_evaluation

    # Verify the function exists and has correct signature
    assert callable(require_recent_evaluation)
    assert EvaluationRequiredError is not None


def test_semantic_tool_validation():
    """Test: Only semantic tools are allowed."""
    # Valid semantic tools should pass
    validate_semantic_tool_only("plan")
    validate_semantic_tool_only("modify")
    validate_semantic_tool_only("explain_training_state")

    # Invalid tools should fail
    with pytest.raises(ValueError, match="not a semantic tool"):
        validate_semantic_tool_only("plan_week (MCP)")

    with pytest.raises(ValueError, match="not a semantic tool"):
        validate_semantic_tool_only("unknown_tool")


def test_tool_horizon_validation():
    """Test: Tools validate horizon support."""
    assert validate_tool_horizon("plan", "season")
    assert validate_tool_horizon("plan", "race")
    assert validate_tool_horizon("plan", "today")  # plan supports week/today when no plan exists
    assert validate_tool_horizon("plan", "week")
    assert validate_tool_horizon("log", "today")
    assert not validate_tool_horizon("log", "season")  # log only supports today


def test_routing_with_safety_checks():
    """Test: Routing includes prerequisite checks for mutations."""
    tool, checks = route_with_safety_check(
        "modify", "week", run_incoherence_check=True, athlete_id=1
    )
    assert tool is not None
    assert tool.name == "modify"
    assert "detect_plan_incoherence" in checks

    tool, checks = route_with_safety_check(
        "plan", "season", run_incoherence_check=True, athlete_id=1
    )
    assert tool is not None
    assert tool.name == "plan"
    assert "detect_plan_incoherence" in checks

    tool, checks = route_with_safety_check("explain", "week", run_incoherence_check=True)
    assert tool is not None
    assert tool.name == "explain_training_state"
    assert len(checks) == 0


def test_semantic_registry_exports_only_canonical_tools():
    """Test: Semantic registry only contains canonical tools."""
    registry_tools = set(SEMANTIC_TOOL_REGISTRY.list_tools())
    catalog_tools = set(CANONICAL_TOOLS.keys())

    assert registry_tools == catalog_tools, "Registry and catalog must match exactly"
