"""Routing tests: plan + week/today → CREATE vs MODIFY by existence only."""

from unittest.mock import patch

import pytest

from app.coach.routing.types import RoutedTool
from app.orchestrator.routing import route_with_safety_check


def _route_plan_week(athlete_id: int | None, has_plan: bool) -> RoutedTool | None:
    with patch("app.orchestrator.routing.has_existing_plan", return_value=has_plan):
        tool, _ = route_with_safety_check(
            intent="plan",
            horizon="week",
            has_proposal=False,
            needs_approval=False,
            athlete_id=athlete_id,
        )
    return tool


def _route_plan_today(athlete_id: int | None, has_plan: bool) -> RoutedTool | None:
    with patch("app.orchestrator.routing.has_existing_plan", return_value=has_plan):
        tool, _ = route_with_safety_check(
            intent="plan",
            horizon="today",
            has_proposal=False,
            needs_approval=False,
            athlete_id=athlete_id,
        )
    return tool


def test_plan_week_no_sessions_routes_to_plan():
    """plan + week + no sessions → plan + CREATE."""
    rt = _route_plan_week(1, has_plan=False)
    assert rt is not None
    assert rt.name == "plan"
    assert rt.mode == "CREATE"


def test_plan_week_existing_sessions_routes_to_modify():
    """plan + week + existing sessions → modify + MODIFY."""
    rt = _route_plan_week(1, has_plan=True)
    assert rt is not None
    assert rt.name == "modify"
    assert rt.mode == "MODIFY"


def test_plan_today_no_sessions_routes_to_plan():
    """plan + today + no sessions → plan + CREATE."""
    rt = _route_plan_today(1, has_plan=False)
    assert rt is not None
    assert rt.name == "plan"
    assert rt.mode == "CREATE"


def test_plan_today_existing_sessions_routes_to_modify():
    """plan + today + existing sessions → modify + MODIFY."""
    rt = _route_plan_today(1, has_plan=True)
    assert rt is not None
    assert rt.name == "modify"
    assert rt.mode == "MODIFY"


def test_plan_week_no_athlete_id_defaults_to_plan():
    """plan + week + no athlete_id → plan + CREATE, has_existing_plan not called."""
    with patch("app.orchestrator.routing.has_existing_plan") as mock:
        tool, _ = route_with_safety_check(
            intent="plan",
            horizon="week",
            has_proposal=False,
            needs_approval=False,
            athlete_id=None,
        )
        mock.assert_not_called()
    assert tool is not None
    assert tool.name == "plan"
    assert tool.mode == "CREATE"
