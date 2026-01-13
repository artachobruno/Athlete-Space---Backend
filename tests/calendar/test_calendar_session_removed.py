"""Test that CalendarSession model has been removed.

This test ensures that the CalendarSession database model has been completely
removed from the codebase as part of the migration to planned_sessions-only architecture.
"""

import pytest

from app.workouts.guards import assert_calendar_session_does_not_exist


def test_calendar_session_model_does_not_exist():
    """Test that CalendarSession model does not exist in app.db.models."""
    import app.db.models as models_module

    # CalendarSession should not exist
    assert not hasattr(models_module, "CalendarSession"), "CalendarSession model still exists in app.db.models"

    # The guard function should not raise
    assert_calendar_session_does_not_exist()


def test_calendar_session_not_importable():
    """Test that CalendarSession cannot be imported from app.db.models."""
    import app.db.models as models_module

    # Should not be able to access CalendarSession
    with pytest.raises(AttributeError):
        _ = models_module.CalendarSession


def test_calendar_session_table_not_referenced():
    """Test that calendar_sessions table is not referenced in model definitions."""
    import app.db.models as models_module

    # Check that no model has __tablename__ = "calendar_sessions"
    for attr_name in dir(models_module):
        attr = getattr(models_module, attr_name)
        if hasattr(attr, "__tablename__"):
            assert attr.__tablename__ != "calendar_sessions", (
                f"Model {attr_name} still references calendar_sessions table"
            )
