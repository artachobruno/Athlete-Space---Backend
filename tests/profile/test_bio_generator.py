"""Unit tests for athlete bio generator."""

import pytest

from app.db.models import AthleteProfile, User
from app.models.athlete_profile import (
    AthleteProfile as AthleteProfileSchema,
)
from app.models.athlete_profile import (
    ExperienceLevel,
    GoalType,
    SportType,
)
from app.services.athlete_bio_generator import (
    _calculate_confidence,
    _generate_fallback_bio,
    generate_athlete_bio,
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


def test_calculate_confidence_no_data():
    """Test that confidence is low with no data."""
    profile = AthleteProfileSchema()
    confidence = _calculate_confidence(profile)

    assert confidence >= 0.0
    assert confidence <= 1.0
    assert confidence < 0.5  # Should be low with no data


def test_calculate_confidence_full_data():
    """Test that confidence is high with full data."""
    profile = AthleteProfileSchema(
        identity={"first_name": "John", "age": 30, "location": "NYC"},
        training_context={
            "primary_sport": SportType.RUN,
            "experience_level": ExperienceLevel.COMPETITIVE,
            "years_training": 5.0,
        },
        goals={
            "primary_goal": "Marathon PR",
            "goal_type": GoalType.PERFORMANCE,
            "target_event": "Boston Marathon",
        },
        constraints={
            "availability_days_per_week": 5,
            "availability_hours_per_week": 10.0,
        },
        preferences={
            "recovery_preference": "active",
            "coaching_style": "flexible",
        },
    )
    confidence = _calculate_confidence(profile)

    assert confidence >= 0.5  # Should be higher with full data
    assert confidence <= 1.0


def test_generate_fallback_bio_empty_profile():
    """Test that fallback bio works with empty profile."""
    profile = AthleteProfileSchema()
    bio_text = _generate_fallback_bio(profile)

    assert bio_text is not None
    assert len(bio_text) > 0
    assert "athlete" in bio_text.lower()


def test_generate_fallback_bio_with_data():
    """Test that fallback bio includes profile data."""
    profile = AthleteProfileSchema(
        identity={"first_name": "John"},
        training_context={"primary_sport": SportType.RUN, "experience_level": ExperienceLevel.STRUCTURED},
        goals={"primary_goal": "Marathon PR"},
        constraints={"availability_days_per_week": 5},
    )
    bio_text = _generate_fallback_bio(profile)

    assert "John" in bio_text
    assert "marathon" in bio_text.lower() or "pr" in bio_text.lower()
