"""Proposal phase tests (Phase A).

Trust invariant: proposal never mutates. No mutation tools. Output includes
scope, blocks/phases, changes, and explicit authorization request.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.coach.flows.plan_proposal import (
    PlanProposal,
    format_proposal_message,
    generate_plan_proposal,
)


@pytest.fixture
def mock_deps():
    """Minimal CoachDeps for proposal tests."""
    from app.coach.agents.orchestrator_deps import CoachDeps

    return CoachDeps(athlete_id=1, user_id="test-user", athlete_state=None)


@pytest.mark.asyncio
async def test_proposal_generation_does_not_create_or_modify_plan(mock_deps):
    """Proposal generation does NOT create or modify a plan."""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(side_effect=Exception("force fallback"))

    with (
        patch("app.coach.flows.plan_proposal.get_model", return_value=MagicMock()),
        patch("app.coach.flows.plan_proposal.Agent", return_value=fake_agent),
    ):
        proposal = await generate_plan_proposal(
            context={"race_date": "2025-04-25", "distance": "marathon"},
            deps=mock_deps,
            horizon="race",
        )

    assert isinstance(proposal, PlanProposal)
    assert proposal.scope in ("week", "season", "race")
    assert len(proposal.blocks) >= 1
    assert len(proposal.changes) >= 1
    assert proposal.authorization_required is True


@pytest.mark.asyncio
async def test_proposal_no_mutation_tools_called(mock_deps):
    """No mutation tools are called during proposal generation."""
    call_tool_called = []

    def track_call(*args, **kwargs):
        call_tool_called.append(1)
        raise RuntimeError("Should not call tools")

    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(side_effect=Exception("force fallback"))

    with (
        patch("app.coach.flows.plan_proposal.get_model", return_value=MagicMock()),
        patch("app.coach.flows.plan_proposal.Agent", return_value=fake_agent),
        patch("app.coach.mcp_client.call_tool", side_effect=track_call),
    ):
        await generate_plan_proposal(
            context={"distance": "5k"},
            deps=mock_deps,
            horizon="week",
        )

    assert len(call_tool_called) == 0


def test_proposal_output_includes_scope_blocks_changes_auth_request():
    """Output includes scope, blocks/phases, changes, and explicit authorization request."""
    proposal = PlanProposal(
        type="plan_proposal",
        scope="race",
        overview="12-week marathon build",
        blocks=[
            {"name": "Base", "description": "Build aerobic base"},
            {"name": "Build", "description": "Introduce quality"},
        ],
        end_goal_date="2025-04-25",
        changes=["Create new race plan", "Replace existing week structure"],
        authorization_required=True,
        assumptions=["5 days/week", "No injuries"],
    )
    msg = format_proposal_message(proposal)

    assert "race" in msg.lower() or "Race" in msg
    assert "Base" in msg and "Build" in msg
    assert "Create new race plan" in msg or "Replace" in msg
    assert "proceed" in msg.lower() or "approve" in msg.lower() or "yes" in msg.lower()
    assert "no" in msg.lower() or "cancel" in msg.lower()
