"""Routing tests: plan + week/today → plan vs modify by existence only."""

from datetime import date
from unittest.mock import patch

import pytest

from app.orchestrator.routing import route_with_safety_check


def _route_plan_week(user_id: str | None, today: date | None, has_plan: bool) -> str | None:
    with patch("app.coach.routing.route.has_existing_plan", return_value=has_plan):
        tool, _ = route_with_safety_check(
            intent="plan",
            horizon="week",
            has_proposal=False,
            needs_approval=False,
            user_id=user_id,
            today=today,
        )
    return tool


def _route_plan_today(user_id: str | None, today: date | None, has_plan: bool) -> str | None:
    with patch("app.coach.routing.route.has_existing_plan", return_value=has_plan):
        tool, _ = route_with_safety_check(
            intent="plan",
            horizon="today",
            has_proposal=False,
            needs_approval=False,
            user_id=user_id,
            today=today,
        )
    return tool


def test_plan_week_no_sessions_routes_to_plan():
    """plan + week + no sessions → routed_tool == 'plan'."""
    assert _route_plan_week("user-1", date(2026, 1, 20), has_plan=False) == "plan"


def test_plan_week_existing_sessions_routes_to_modify():
    """plan + week + existing sessions → routed_tool == 'modify'."""
    assert _route_plan_week("user-1", date(2026, 1, 20), has_plan=True) == "modify"


def test_plan_today_no_sessions_routes_to_plan():
    """plan + today + no sessions → routed_tool == 'plan' (mirrors week)."""
    assert _route_plan_today("user-1", date(2026, 1, 20), has_plan=False) == "plan"


def test_plan_today_existing_sessions_routes_to_modify():
    """plan + today + existing sessions → routed_tool == 'modify' (mirrors week)."""
    assert _route_plan_today("user-1", date(2026, 1, 20), has_plan=True) == "modify"


def test_plan_week_no_user_id_defaults_to_plan():
    """plan + week + no user_id → 'plan' (cannot run existence check)."""
    with patch("app.coach.routing.route.has_existing_plan") as mock:
        tool, _ = route_with_safety_check(
            intent="plan",
            horizon="week",
            has_proposal=False,
            needs_approval=False,
            user_id=None,
            today=date(2026, 1, 20),
        )
        mock.assert_not_called()
    assert tool == "plan"


def test_plan_week_no_today_defaults_to_plan():
    """plan + week + no today → 'plan' (cannot run existence check)."""
    with patch("app.coach.routing.route.has_existing_plan") as mock:
        tool, _ = route_with_safety_check(
            intent="plan",
            horizon="week",
            has_proposal=False,
            needs_approval=False,
            user_id="user-1",
            today=None,
        )
        mock.assert_not_called()
    assert tool == "plan"
