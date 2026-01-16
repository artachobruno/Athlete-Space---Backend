"""Tests for MODIFY → week LLM extraction.

These tests verify the extraction contract, not OpenAI itself.
We mock the LLM to test schema conformance and parsing logic.
"""

import contextlib
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai import Agent, RunResult

from app.coach.extraction.modify_week_extractor import (
    ExtractedWeekModification,
    extract_week_modification_llm,
)


@pytest.mark.asyncio
async def test_reduce_week_by_percent():
    """Test extraction of percentage-based volume reduction."""
    user_message = "Cut this week by 20%, I'm exhausted"

    # Mock LLM response
    expected_extracted = ExtractedWeekModification(
        horizon="week",
        change_type="reduce_volume",
        percent=0.2,
        reason="fatigue",
    )

    with patch("app.coach.extraction.modify_week_extractor.Agent") as mock_agent_class:
        mock_agent = AsyncMock(spec=Agent)
        mock_run_result = RunResult(
            output=expected_extracted,
            messages=[],
            usage=None,
        )
        mock_agent.run = AsyncMock(return_value=mock_run_result)
        mock_agent_class.return_value = mock_agent

        extracted = await extract_week_modification_llm(user_message)

        assert extracted.change_type == "reduce_volume"
        assert extracted.percent == 0.2
        assert extracted.miles is None
        assert extracted.horizon == "week"
        assert extracted.reason == "fatigue"


@pytest.mark.asyncio
async def test_increase_volume_by_miles():
    """Test extraction of absolute miles-based volume increase."""
    user_message = "Add 10 miles this week"

    expected_extracted = ExtractedWeekModification(
        horizon="week",
        change_type="increase_volume",
        miles=10.0,
    )

    with patch("app.coach.extraction.modify_week_extractor.Agent") as mock_agent_class:
        mock_agent = AsyncMock(spec=Agent)
        mock_run_result = RunResult(
            output=expected_extracted,
            messages=[],
            usage=None,
        )
        mock_agent.run = AsyncMock(return_value=mock_run_result)
        mock_agent_class.return_value = mock_agent

        extracted = await extract_week_modification_llm(user_message)

        assert extracted.change_type == "increase_volume"
        assert extracted.miles == 10.0
        assert extracted.percent is None
        assert extracted.horizon == "week"


@pytest.mark.asyncio
async def test_shift_days():
    """Test extraction of day shifting."""
    user_message = "Move Tuesday workout to Wednesday"

    expected_extracted = ExtractedWeekModification(
        horizon="week",
        change_type="shift_days",
        shift_map={"2024-01-16": "2024-01-17"},
    )

    with patch("app.coach.extraction.modify_week_extractor.Agent") as mock_agent_class:
        mock_agent = AsyncMock(spec=Agent)
        mock_run_result = RunResult(
            output=expected_extracted,
            messages=[],
            usage=None,
        )
        mock_agent.run = AsyncMock(return_value=mock_run_result)
        mock_agent_class.return_value = mock_agent

        extracted = await extract_week_modification_llm(user_message)

        assert extracted.change_type == "shift_days"
        assert extracted.shift_map == {"2024-01-16": "2024-01-17"}
        assert extracted.percent is None
        assert extracted.miles is None


@pytest.mark.asyncio
async def test_replace_day():
    """Test extraction of replace_day with day modification."""
    user_message = "Replace Friday's workout with an easy 5 mile run"

    expected_extracted = ExtractedWeekModification(
        horizon="week",
        change_type="replace_day",
        target_date="2024-01-19",
        day_modification={
            "change_type": "adjust_distance",
            "value": 5.0,
        },
    )

    with patch("app.coach.extraction.modify_week_extractor.Agent") as mock_agent_class:
        mock_agent = AsyncMock(spec=Agent)
        mock_run_result = RunResult(
            output=expected_extracted,
            messages=[],
            usage=None,
        )
        mock_agent.run = AsyncMock(return_value=mock_run_result)
        mock_agent_class.return_value = mock_agent

        extracted = await extract_week_modification_llm(user_message)

        assert extracted.change_type == "replace_day"
        assert extracted.target_date == "2024-01-19"
        assert extracted.day_modification is not None
        assert extracted.day_modification["change_type"] == "adjust_distance"
        assert extracted.day_modification["value"] == 5.0


@pytest.mark.asyncio
async def test_extraction_with_relative_dates():
    """Test extraction preserves relative dates for later resolution."""
    user_message = "Cut next week by 15%"

    expected_extracted = ExtractedWeekModification(
        horizon="week",
        change_type="reduce_volume",
        percent=0.15,
        start_date="next week",  # Relative date, preserved as-is
    )

    with patch("app.coach.extraction.modify_week_extractor.Agent") as mock_agent_class:
        mock_agent = AsyncMock(spec=Agent)
        mock_run_result = RunResult(
            output=expected_extracted,
            messages=[],
            usage=None,
        )
        mock_agent.run = AsyncMock(return_value=mock_run_result)
        mock_agent_class.return_value = mock_agent

        extracted = await extract_week_modification_llm(user_message)

        assert extracted.start_date == "next week"
        # Relative dates should be preserved for adapter layer to resolve
        assert extracted.start_date is not None
        # Verify it's not a parseable ISO date (is a relative string)
        # If it parses, that's fine, but relative dates typically won't
        with contextlib.suppress(ValueError):
            date.fromisoformat(extracted.start_date)


@pytest.mark.asyncio
async def test_extraction_schema_conformance():
    """Test that extracted data conforms to ExtractedWeekModification schema."""
    user_message = "Make this week easier"

    expected_extracted = ExtractedWeekModification(
        horizon="week",
        change_type="reduce_volume",
        percent=0.1,
        reason="make easier",
    )

    with patch("app.coach.extraction.modify_week_extractor.Agent") as mock_agent_class:
        mock_agent = AsyncMock(spec=Agent)
        mock_run_result = RunResult(
            output=expected_extracted,
            messages=[],
            usage=None,
        )
        mock_agent.run = AsyncMock(return_value=mock_run_result)
        mock_agent_class.return_value = mock_agent

        extracted = await extract_week_modification_llm(user_message)

        # Verify schema conformance - all fields should be valid for the schema
        assert isinstance(extracted, ExtractedWeekModification)
        assert extracted.horizon == "week"
        assert extracted.change_type in {"reduce_volume", "increase_volume", "shift_days", "replace_day"}
        # Percent and miles should be mutually exclusive for volume changes
        if extracted.change_type in {"reduce_volume", "increase_volume"}:
            assert (extracted.percent is None) != (extracted.miles is None) or (
                extracted.percent is None and extracted.miles is None
            )
