"""Tests for plan context validation.

Tests validate_plan_context function to ensure:
- Race plans require race_distance
- Season plans must not have race_distance
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.planner.enums import PlanType, RaceDistance, TrainingIntent
from app.planner.errors import InvalidPlanContextError
from app.planner.models import PlanContext
from app.planner.validators import validate_plan_context


def test_race_context_requires_distance() -> None:
    """Test that race plan without race_distance raises error."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=None,
    )
    with pytest.raises(InvalidPlanContextError, match="Race plan requires race_distance"):
        validate_plan_context(ctx)


def test_season_context_must_not_have_distance() -> None:
    """Test that season plan with race_distance raises error."""
    ctx = PlanContext(
        plan_type=PlanType.SEASON,
        intent=TrainingIntent.MAINTAIN,
        weeks=8,
        race_distance=RaceDistance.MARATHON,
    )
    with pytest.raises(InvalidPlanContextError, match="Season plan must not specify race_distance"):
        validate_plan_context(ctx)


def test_race_context_with_distance_valid() -> None:
    """Test that race plan with race_distance is valid."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.MARATHON,
        target_date="2024-06-15",
    )
    # Should not raise
    validate_plan_context(ctx)


def test_season_context_without_distance_valid() -> None:
    """Test that season plan without race_distance is valid."""
    ctx = PlanContext(
        plan_type=PlanType.SEASON,
        intent=TrainingIntent.EXPLORE,
        weeks=8,
        race_distance=None,
    )
    # Should not raise
    validate_plan_context(ctx)
