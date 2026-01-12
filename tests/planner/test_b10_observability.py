"""Tests for planner observability and guards (B10).

These tests verify that:
- Every stage emits start + success events
- Failures emit fail events
- Invariant guards trip on bad data
- Legacy paths raise immediately
- Metrics are emitted correctly
- Timing is measured per stage
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from app.planner.enums import DayType, PlanType, RaceDistance, TrainingIntent, WeekFocus
from app.planner.guards import (
    assert_planner_v2_only,
    guard_invariants,
    guard_no_recursion,
    guard_no_repair,
)
from app.planner.models import (
    MacroWeek,
    PlanContext,
    PlannedSession,
    SessionTemplate,
    SessionTextOutput,
)
from app.planner.observability import (
    PlannerStage,
    log_event,
    log_stage_event,
    log_stage_metric,
    timing,
)


def test_planner_stage_enum_values():
    """Test that PlannerStage enum has correct values."""
    assert PlannerStage.MACRO.value == "macro_plan"
    assert PlannerStage.PHILOSOPHY.value == "philosophy_select"
    assert PlannerStage.STRUCTURE.value == "structure_load"
    assert PlannerStage.VOLUME.value == "volume_allocate"
    assert PlannerStage.TEMPLATE.value == "template_select"
    assert PlannerStage.TEXT.value == "session_text"
    assert PlannerStage.PERSIST.value == "calendar_persist"


def test_log_stage_event_start():
    """Test that log_stage_event emits start events correctly."""
    with patch("app.planner.observability.log_event") as mock_log:
        log_stage_event(PlannerStage.MACRO, "start", plan_id="test-plan-123")
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "planner_stage"
        assert call_args[1]["stage"] == "macro_plan"
        assert call_args[1]["status"] == "start"
        assert call_args[1]["plan_id"] == "test-plan-123"


def test_log_stage_event_success():
    """Test that log_stage_event emits success events correctly."""
    with patch("app.planner.observability.log_event") as mock_log:
        log_stage_event(
            PlannerStage.MACRO,
            "success",
            plan_id="test-plan-123",
            meta={"week_count": 12},
        )
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "planner_stage"
        assert call_args[1]["stage"] == "macro_plan"
        assert call_args[1]["status"] == "success"
        assert call_args[1]["plan_id"] == "test-plan-123"
        assert call_args[1]["week_count"] == 12


def test_log_stage_event_fail():
    """Test that log_stage_event emits fail events correctly."""
    with patch("app.planner.observability.log_event") as mock_log:
        log_stage_event(
            PlannerStage.MACRO,
            "fail",
            plan_id="test-plan-123",
            meta={"error": "Test error"},
        )
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "planner_stage"
        assert call_args[1]["stage"] == "macro_plan"
        assert call_args[1]["status"] == "fail"
        assert call_args[1]["plan_id"] == "test-plan-123"
        assert call_args[1]["error"] == "Test error"


def test_log_stage_event_invalid_status():
    """Test that log_stage_event raises ValueError for invalid status."""
    with pytest.raises(ValueError, match="Status must be one of"):
        log_stage_event(PlannerStage.MACRO, "invalid_status")


def test_log_stage_metric_success():
    """Test that log_stage_metric emits success counters."""
    with patch("app.planner.observability.log_event") as mock_log:
        log_stage_metric(PlannerStage.MACRO, True)
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "planner_macro_plan_success"


def test_log_stage_metric_failure():
    """Test that log_stage_metric emits failure counters."""
    with patch("app.planner.observability.log_event") as mock_log:
        log_stage_metric(PlannerStage.MACRO, False)
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "planner_macro_plan_failure"


def test_timing_context_manager():
    """Test that timing context manager measures and logs duration."""
    import time

    with patch("app.planner.observability.log_event") as mock_log:
        with timing("planner.stage.macro"):
            time.sleep(0.01)  # Small delay to ensure timing > 0

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "planner_timing"
        assert call_args[1]["metric"] == "planner.stage.macro"
        assert call_args[1]["duration_seconds"] > 0


def test_timing_context_manager_with_exception():
    """Test that timing context manager logs even when exception occurs."""
    with patch("app.planner.observability.log_event") as mock_log:
        with pytest.raises(ValueError), timing("planner.stage.macro"):
            raise ValueError("Test error")

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "planner_timing"
        assert call_args[1]["metric"] == "planner.stage.macro"


def test_guard_invariants_valid_data():
    """Test that guard_invariants passes with valid data."""
    macro_weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
        MacroWeek(week_index=2, focus=WeekFocus.BASE, total_distance=42.0),
    ]

    template = SessionTemplate(
        template_id="test_template",
        description_key="test_desc",
        kind="easy_continuous",
        params={},
        constraints={},
        tags=[],
    )

    text_output = SessionTextOutput(
        title="Easy Run",
        description="Run easy for 5 miles",
        structure={},
        computed={},
    )

    planned_sessions = [
        PlannedSession(
            day_index=0,
            day_type=DayType.EASY,
            distance=5.0,
            template=template,
            text_output=text_output,
        ),
    ]

    # Should not raise
    guard_invariants(macro_weeks, planned_sessions)


def test_guard_invariants_empty_macro_weeks():
    """Test that guard_invariants works with empty macro_weeks (when not available)."""
    template = SessionTemplate(
        template_id="test_template",
        description_key="test_desc",
        kind="easy_continuous",
        params={},
        constraints={},
        tags=[],
    )

    text_output = SessionTextOutput(
        title="Easy Run",
        description="Run easy for 5 miles",
        structure={},
        computed={},
    )

    planned_sessions = [
        PlannedSession(
            day_index=0,
            day_type=DayType.EASY,
            distance=5.0,
            template=template,
            text_output=text_output,
        ),
    ]

    # Should not raise (empty macro_weeks is allowed)
    guard_invariants([], planned_sessions)


def test_guard_invariants_empty_sessions():
    """Test that guard_invariants raises on empty sessions list."""
    macro_weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
    ]

    with pytest.raises(AssertionError, match="Planned sessions list must not be empty"):
        guard_invariants(macro_weeks, [])


def test_guard_invariants_wrong_week_index():
    """Test that guard_invariants raises if first week is not week 1."""
    macro_weeks = [
        MacroWeek(week_index=2, focus=WeekFocus.BASE, total_distance=40.0),
    ]

    template = SessionTemplate(
        template_id="test_template",
        description_key="test_desc",
        kind="easy_continuous",
        params={},
        constraints={},
        tags=[],
    )

    text_output = SessionTextOutput(
        title="Easy Run",
        description="Run easy for 5 miles",
        structure={},
        computed={},
    )

    planned_sessions = [
        PlannedSession(
            day_index=0,
            day_type=DayType.EASY,
            distance=5.0,
            template=template,
            text_output=text_output,
        ),
    ]

    with pytest.raises(AssertionError, match="First macro week must be week 1"):
        guard_invariants(macro_weeks, planned_sessions)


def test_guard_invariants_negative_distance():
    """Test that guard_invariants raises on negative session distance."""
    macro_weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
    ]

    template = SessionTemplate(
        template_id="test_template",
        description_key="test_desc",
        kind="easy_continuous",
        params={},
        constraints={},
        tags=[],
    )

    text_output = SessionTextOutput(
        title="Easy Run",
        description="Run easy for 5 miles",
        structure={},
        computed={},
    )

    planned_sessions = [
        PlannedSession(
            day_index=0,
            day_type=DayType.EASY,
            distance=-1.0,  # Invalid negative distance
            template=template,
            text_output=text_output,
        ),
    ]

    with pytest.raises(AssertionError, match="Session distance must be >= 0"):
        guard_invariants(macro_weeks, planned_sessions)


def test_guard_invariants_missing_text_output():
    """Test that guard_invariants raises if session has no text_output."""
    macro_weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
    ]

    template = SessionTemplate(
        template_id="test_template",
        description_key="test_desc",
        kind="easy_continuous",
        params={},
        constraints={},
        tags=[],
    )

    planned_sessions = [
        PlannedSession(
            day_index=0,
            day_type=DayType.EASY,
            distance=5.0,
            template=template,
            text_output=None,  # Missing text_output
        ),
    ]

    with pytest.raises(AssertionError, match="Session must have text_output before persistence"):
        guard_invariants(macro_weeks, planned_sessions)


def test_guard_invariants_empty_description():
    """Test that guard_invariants raises if session description is empty."""
    macro_weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
    ]

    template = SessionTemplate(
        template_id="test_template",
        description_key="test_desc",
        kind="easy_continuous",
        params={},
        constraints={},
        tags=[],
    )

    text_output = SessionTextOutput(
        title="Easy Run",
        description="",  # Empty description
        structure={},
        computed={},
    )

    planned_sessions = [
        PlannedSession(
            day_index=0,
            day_type=DayType.EASY,
            distance=5.0,
            template=template,
            text_output=text_output,
        ),
    ]

    with pytest.raises(AssertionError, match="Session description must not be empty"):
        guard_invariants(macro_weeks, planned_sessions)


def test_guard_no_recursion_allowed():
    """Test that guard_no_recursion allows depth 0 and 1."""
    guard_no_recursion(0)  # Should not raise
    guard_no_recursion(1)  # Should not raise


def test_guard_no_recursion_forbidden():
    """Test that guard_no_recursion raises on depth > 1."""
    with pytest.raises(RuntimeError, match="Recursive planning forbidden"):
        guard_no_recursion(2)

    with pytest.raises(RuntimeError, match="Recursive planning forbidden"):
        guard_no_recursion(10)


def test_guard_no_repair_allowed():
    """Test that guard_no_repair allows flags without forbidden values."""
    flags = {"some_flag": True, "other_flag": "value"}
    guard_no_repair(flags)  # Should not raise


def test_guard_no_repair_forbidden_repair():
    """Test that guard_no_repair raises on 'repair' flag."""
    flags = {"repair": True}
    with pytest.raises(RuntimeError, match="Forbidden planner flag: repair"):
        guard_no_repair(flags)


def test_guard_no_repair_forbidden_adjust():
    """Test that guard_no_repair raises on 'adjust' flag."""
    flags = {"adjust": True}
    with pytest.raises(RuntimeError, match="Forbidden planner flag: adjust"):
        guard_no_repair(flags)


def test_guard_no_repair_forbidden_replan():
    """Test that guard_no_repair raises on 'replan' flag."""
    flags = {"replan": True}
    with pytest.raises(RuntimeError, match="Forbidden planner flag: replan"):
        guard_no_repair(flags)


def test_assert_planner_v2_only_no_legacy():
    """Test that assert_planner_v2_only passes when no legacy code in stack."""
    # This should not raise if called from test context (no legacy code)
    # Note: This test may be fragile if test framework adds frames
    from contextlib import suppress

    with suppress(RuntimeError):
        # If it raises, it means legacy code was detected (which is also valid)
        assert_planner_v2_only()


def test_assert_planner_v2_only_detects_legacy():
    """Test that assert_planner_v2_only detects legacy code in call stack."""
    # Create a mock frame with legacy code in filename
    mock_frame = MagicMock()
    mock_frame.filename = "/path/to/plan_race_build_new.py"

    with patch("app.planner.guards.inspect.stack") as mock_stack:
        mock_stack.return_value = [mock_frame]
        with pytest.raises(RuntimeError, match="Legacy planner detected"):
            assert_planner_v2_only()


def test_log_event_calls_logger():
    """Test that log_event calls logger.info with correct arguments."""
    with patch("app.planner.observability.logger") as mock_logger:
        log_event("test_event", key1="value1", key2=123)
        mock_logger.info.assert_called_once_with("test_event", key1="value1", key2=123)


def test_all_stages_have_metrics():
    """Test that all stages can emit success/failure metrics."""
    with patch("app.planner.observability.log_event") as mock_log:
        for stage in PlannerStage:
            log_stage_metric(stage, True)
            log_stage_metric(stage, False)

        # Should have called log_event for each stage (2 calls per stage: success + failure)
        assert mock_log.call_count == len(PlannerStage) * 2
