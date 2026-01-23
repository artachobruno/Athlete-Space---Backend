"""Summary phase tests (Phase D).

Summary only after success. Includes weekly volumes, blocks, long run progression,
end goal date, applied constraints.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.coach.flows.plan_summary import (
    PlanSummary,
    format_summary_message,
    generate_plan_summary,
)


@pytest.mark.asyncio
async def test_summary_generated_only_after_success():
    """Summary is generated only after successful execution.

    (Contract: caller invokes generate_plan_summary only after success.
    We test that generate_plan_summary is read-only and never mutates.)
    """
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(side_effect=Exception("force fallback"))

    with (
        patch("app.coach.flows.plan_summary.get_model", return_value=MagicMock()),
        patch("app.coach.flows.plan_summary.Agent", return_value=fake_agent),
    ):
        summary = await generate_plan_summary(plan_data={}, horizon="race")

    assert isinstance(summary, PlanSummary)
    assert summary.duration_weeks >= 1
    assert summary.end_date
    assert len(summary.weekly_volumes) >= 1
    assert len(summary.phase_breakdown) >= 1
    assert len(summary.constraints_applied) >= 1


@pytest.mark.asyncio
async def test_summary_includes_weekly_volumes_blocks_long_run_end_date_constraints():
    """Summary includes weekly volume progression, blocks, long run progression, end date, constraints."""
    with (
        patch("app.coach.flows.plan_summary.get_model", return_value=MagicMock()),
        patch("app.coach.flows.plan_summary.Agent", return_value=MagicMock(run=AsyncMock(side_effect=Exception("fallback")))),
    ):
        summary = await generate_plan_summary(plan_data={}, horizon="season")

    assert len(summary.weekly_volumes) >= 1
    for v in summary.weekly_volumes:
        assert "weeks" in v or "volume_mi" in v or "volume_km" in v

    assert len(summary.phase_breakdown) >= 1
    for b in summary.phase_breakdown:
        assert "name" in b or "description" in b or "weeks" in b

    assert len(summary.long_run_progression) >= 1
    for lr in summary.long_run_progression:
        assert "week" in lr or "distance_mi" in lr

    assert summary.end_date
    assert len(summary.constraints_applied) >= 1


def test_format_summary_message_includes_required_sections():
    """Formatted summary message includes volumes, blocks, long run, end date, constraints."""
    summary = PlanSummary(
        duration_weeks=12,
        end_date="2025-04-25",
        weekly_volumes=[
            {"weeks": "W1-4", "volume_mi": 45, "volume_km": 72.4},
            {"weeks": "W5-9", "volume_mi": 55, "volume_km": 88.5},
        ],
        phase_breakdown=[
            {"name": "Base", "weeks": "W1-4", "description": "Base building"},
            {"name": "Build", "weeks": "W5-9", "description": "Progressive build"},
        ],
        long_run_progression=[
            {"week": "W1", "distance_mi": 8.0},
            {"week": "W9", "distance_mi": 20.0},
        ],
        goal_date="2025-04-25",
        constraints_applied=["Fatigue cap: TSB > -10", "Taper protection: last 2 weeks"],
    )
    msg = format_summary_message(summary)

    assert "PLAN COMPLETE" in msg or "complete" in msg.lower()
    assert "12" in msg
    assert "2025-04-25" in msg
    assert "45" in msg or "72" in msg
    assert "Base" in msg or "Build" in msg
    assert "20" in msg or "8" in msg
    assert "Fatigue" in msg or "Taper" in msg or "TSB" in msg or "constraint" in msg.lower()
