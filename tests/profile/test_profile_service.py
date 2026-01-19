"""Unit tests for athlete profile service."""

from datetime import datetime, timezone

import pytest

from app.db.models import AthleteBio, AthleteProfile, User
from app.models.athlete_profile import (
    AthleteProfile as AthleteProfileSchema,
)
from app.models.athlete_profile import (
    GoalType,
    SportType,
)
from app.services.athlete_profile_service import (
    compute_profile_hash,
    get_or_create_profile,
    get_profile_schema,
    update_structured_profile,
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


def test_get_or_create_profile_creates_if_not_exists(db_session, test_user_id):
    """Test that get_or_create_profile creates a new profile if it doesn't exist."""
    profile = get_or_create_profile(db_session, test_user_id)

    assert profile is not None
    assert profile.user_id == test_user_id
    assert profile.identity is None
    assert profile.goals is None


def test_get_or_create_profile_returns_existing(db_session, test_user_id):
    """Test that get_or_create_profile returns existing profile."""
    # Create profile manually
    profile = AthleteProfile(user_id=test_user_id, identity={"first_name": "John"})
    db_session.add(profile)
    db_session.commit()

    # Get it
    retrieved = get_or_create_profile(db_session, test_user_id)

    assert retrieved.user_id == test_user_id
    assert retrieved.identity == {"first_name": "John"}


def test_update_structured_profile_partial_update(db_session, test_user_id):
    """Test that update_structured_profile performs partial updates."""
    # Create profile
    profile = get_or_create_profile(db_session, test_user_id)
    profile.identity = {"first_name": "John", "age": 30}
    db_session.commit()

    # Partial update
    update_structured_profile(
        db_session,
        test_user_id,
        {"identity": {"last_name": "Doe"}},
    )

    # Verify merge
    updated = db_session.query(AthleteProfile).filter_by(user_id=test_user_id).first()
    assert updated.identity == {"first_name": "John", "age": 30, "last_name": "Doe"}


def test_update_structured_profile_creates_if_not_exists(db_session, test_user_id):
    """Test that update_structured_profile creates profile if it doesn't exist."""
    update_structured_profile(
        db_session,
        test_user_id,
        {"goals": {"primary_goal": "Marathon PR"}},
    )

    profile = db_session.query(AthleteProfile).filter_by(user_id=test_user_id).first()
    assert profile is not None
    assert profile.goals == {"primary_goal": "Marathon PR"}


def test_compute_profile_hash_excludes_bio(db_session, test_user_id):
    """Test that compute_profile_hash excludes narrative_bio."""
    # Create profile with data
    profile = get_or_create_profile(db_session, test_user_id)
    profile.identity = {"first_name": "John"}
    profile.goals = {"primary_goal": "Marathon"}
    db_session.commit()

    # Compute hash
    hash1 = compute_profile_hash(profile)

    # Update bio (shouldn't affect hash)
    bio = AthleteBio(
        id="test-bio-1",
        user_id=test_user_id,
        text="Test bio",
        confidence_score=0.8,
        source="ai_generated",
    )
    db_session.add(bio)
    db_session.commit()

    # Hash should be same
    hash2 = compute_profile_hash(profile)
    assert hash1 == hash2


def test_compute_profile_hash_changes_with_profile_data(db_session, test_user_id):
    """Test that compute_profile_hash changes when profile data changes."""
    profile = get_or_create_profile(db_session, test_user_id)
    profile.identity = {"first_name": "John"}
    db_session.commit()

    hash1 = compute_profile_hash(profile)

    # Update profile
    profile.goals = {"primary_goal": "Marathon"}
    db_session.commit()

    hash2 = compute_profile_hash(profile)
    assert hash1 != hash2


def test_get_profile_schema_returns_schema(db_session, test_user_id):
    """Test that get_profile_schema returns a Pydantic schema."""
    # Create profile with data
    profile = get_or_create_profile(db_session, test_user_id)
    profile.identity = {"first_name": "John", "age": 30}
    profile.goals = {"primary_goal": "Marathon", "goal_type": "performance"}
    profile.training_context = {"primary_sport": "run", "experience_level": "structured"}
    db_session.commit()

    # Get schema
    schema = get_profile_schema(db_session, test_user_id)

    assert isinstance(schema, AthleteProfileSchema)
    assert schema.identity.first_name == "John"
    assert schema.identity.age == 30
    assert schema.goals.primary_goal == "Marathon"


def test_get_profile_schema_includes_bio_if_exists(db_session, test_user_id):
    """Test that get_profile_schema includes bio if it exists."""
    # Create profile
    get_or_create_profile(db_session, test_user_id)
    db_session.commit()

    # Create bio
    bio = AthleteBio(
        id="test-bio-1",
        user_id=test_user_id,
        text="Test bio text",
        confidence_score=0.8,
        source="ai_generated",
    )
    db_session.add(bio)
    db_session.commit()

    # Get schema
    schema = get_profile_schema(db_session, test_user_id)

    assert schema.narrative_bio is not None
    assert schema.narrative_bio.text == "Test bio text"
    assert schema.narrative_bio.confidence_score == 0.8
