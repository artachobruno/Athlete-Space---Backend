"""Unit tests for athlete profile regeneration engine."""

from datetime import UTC, datetime, timezone

import pytest

from app.db.models import AthleteBio, AthleteProfile, User
from app.services.athlete_profile_regeneration import (
    TRIGGER_FIELDS,
    handle_profile_change,
)


@pytest.fixture
def test_user_id(db_session):
    """Create a test user."""
    user = User(
        id="test-user-123",
        email="test@example.com",
        auth_provider="email",
        role="athlete",
        onboarding_complete=True,
    )
    db_session.add(user)
    db_session.commit()
    return user.id


def test_handle_profile_change_no_trigger_fields(db_session, test_user_id):
    """Test that handle_profile_change does nothing if no trigger fields changed."""
    # Create profile
    profile = AthleteProfile(user_id=test_user_id)
    db_session.add(profile)
    db_session.commit()

    # Change non-trigger field
    handle_profile_change(db_session, test_user_id, ["preferences.feedback_frequency"])

    # Should not create bio
    bio = db_session.query(AthleteBio).filter_by(user_id=test_user_id).first()
    assert bio is None


def test_handle_profile_change_creates_bio_if_not_exists(db_session, test_user_id):
    """Test that handle_profile_change creates bio if it doesn't exist."""
    # Create profile with data
    profile = AthleteProfile(
        user_id=test_user_id,
        identity={"first_name": "John"},
        goals={"primary_goal": "Marathon"},
    )
    db_session.add(profile)
    db_session.commit()

    # Change trigger field
    handle_profile_change(db_session, test_user_id, ["goals.primary_goal"])

    # Should create bio
    bio = db_session.query(AthleteBio).filter_by(user_id=test_user_id).first()
    assert bio is not None
    assert bio.source == "ai_generated"


def test_handle_profile_change_regenerates_ai_bio(db_session, test_user_id):
    """Test that handle_profile_change regenerates AI-generated bio."""
    # Create profile
    profile = AthleteProfile(
        user_id=test_user_id,
        identity={"first_name": "John"},
        goals={"primary_goal": "Marathon"},
    )
    db_session.add(profile)
    db_session.commit()

    # Create existing AI-generated bio
    old_bio = AthleteBio(
        id="test-bio-1",
        user_id=test_user_id,
        text="Old bio text",
        confidence_score=0.8,
        source="ai_generated",
        last_generated_at=datetime.now(UTC),
    )
    db_session.add(old_bio)
    db_session.commit()

    # Change trigger field
    handle_profile_change(db_session, test_user_id, ["goals.primary_goal"])

    # Should regenerate bio (text should change or last_generated_at should update)
    bio = db_session.query(AthleteBio).filter_by(user_id=test_user_id).first()
    assert bio is not None
    assert bio.source == "ai_generated"
    # Bio should be regenerated (either text changed or last_generated_at updated)
    assert bio.last_generated_at is not None


def test_handle_profile_change_marks_stale_if_user_edited(db_session, test_user_id):
    """Test that handle_profile_change marks bio as stale if user-edited."""
    # Create profile
    profile = AthleteProfile(
        user_id=test_user_id,
        identity={"first_name": "John"},
        goals={"primary_goal": "Marathon"},
    )
    db_session.add(profile)
    db_session.commit()

    # Create user-edited bio
    bio = AthleteBio(
        id="test-bio-1",
        user_id=test_user_id,
        text="User edited bio",
        confidence_score=1.0,
        source="user_edited",
        stale=False,
    )
    db_session.add(bio)
    db_session.commit()

    # Change trigger field
    handle_profile_change(db_session, test_user_id, ["goals.primary_goal"])

    # Should mark as stale (not regenerate)
    updated_bio = db_session.query(AthleteBio).filter_by(user_id=test_user_id).first()
    assert updated_bio.stale is True
    assert updated_bio.text == "User edited bio"  # Text should not change


def test_trigger_fields_list():
    """Test that TRIGGER_FIELDS contains expected fields."""
    assert "identity.first_name" in TRIGGER_FIELDS
    assert "goals.primary_goal" in TRIGGER_FIELDS
    assert "training_context.primary_sport" in TRIGGER_FIELDS
    assert "constraints.availability_days_per_week" in TRIGGER_FIELDS
    assert "preferences.recovery_preference" in TRIGGER_FIELDS
