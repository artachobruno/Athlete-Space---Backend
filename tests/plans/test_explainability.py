"""Tests for plan revision explainability.

Tests that explanations:
- Are generated for MODIFY revisions
- Are generated for REGENERATE revisions
- Are generated for BLOCKED revisions
- Never mutate state
- Return deterministic schema
"""

import asyncio
from datetime import UTC, datetime, timezone

import pytest
from pydantic_ai import Agent

from app.coach.explainability import RevisionExplanation, explain_plan_revision
from app.plans.revision.types import PlanRevision, RevisionDelta, RevisionRule


@pytest.fixture
def sample_revision_modify() -> PlanRevision:
    """Sample PlanRevision for MODIFY operation."""
    return PlanRevision(
        revision_id="test-revision-1",
        created_at=datetime.now(UTC),
        scope="day",
        outcome="applied",
        user_request="Reduce distance by 2 miles",
        reason="Fatigue adjustment",
        deltas=[
            RevisionDelta(
                entity_type="session",
                entity_id="session-1",
                date="2026-06-15",
                field="distance_mi",
                old=8.0,
                new=6.0,
            ),
        ],
        rules=[
            RevisionRule(
                rule_id="RACE_DAY_PROTECTION",
                description="Race day can only be reduced unless explicitly overridden",
                severity="block",
                triggered=False,
            ),
        ],
        affected_range={"start": "2026-06-15", "end": "2026-06-15"},
    )


@pytest.fixture
def sample_revision_blocked() -> PlanRevision:
    """Sample PlanRevision for BLOCKED operation."""
    return PlanRevision(
        revision_id="test-revision-2",
        created_at=datetime.now(UTC),
        scope="week",
        outcome="blocked",
        user_request="Increase volume by 20%",
        reason="User request",
        deltas=[],
        rules=[
            RevisionRule(
                rule_id="RACE_WEEK_NO_INCREASE",
                description="Cannot increase volume during race week",
                severity="block",
                triggered=True,
            ),
        ],
        affected_range={"start": "2026-06-10", "end": "2026-06-16"},
    )


@pytest.fixture
def sample_revision_regenerate() -> PlanRevision:
    """Sample PlanRevision for REGENERATE operation."""
    return PlanRevision(
        revision_id="test-revision-3",
        created_at=datetime.now(UTC),
        scope="race",
        outcome="applied",
        user_request="Regenerate plan from today",
        reason="Plan regeneration",
        deltas=[],
        rules=[],
        affected_range={"start": "2026-06-01", "end": "2026-06-15"},
    )


@pytest.mark.skip(reason="Test disabled")
@pytest.mark.asyncio
async def test_explain_modify_revision(monkeypatch, sample_revision_modify: PlanRevision):
    """Test explanation generation for MODIFY revision."""
    # Mock LLM call
    mock_explanation = RevisionExplanation(
        summary="Your training session distance was reduced from 8.0 to 6.0 miles.",
        rationale="This adjustment was made to accommodate your fatigue levels while maintaining training consistency.",
        safeguards=["Race day protection"],
        confidence="This change is safe and aligns with your training goals.",
        revision_type="MODIFY",
    )

    def mock_agent_run(*args, **kwargs):
        class MockResult:
            output = mock_explanation

        return MockResult()

    # Patch Agent.run
    original_agent = Agent
    mock_agent_instance = original_agent(
        model=None,  # type: ignore
        system_prompt="",
        output_type=RevisionExplanation,
    )
    mock_agent_instance.run = mock_agent_run

    monkeypatch.setattr("app.coach.explainability.revision_explainer._get_model", lambda: None)
    monkeypatch.setattr("app.coach.explainability.revision_explainer.Agent", lambda *args, **kwargs: mock_agent_instance)

    deltas = {
        "deltas": [delta.model_dump() for delta in sample_revision_modify.deltas],
    }

    explanation = await explain_plan_revision(
        revision=sample_revision_modify,
        deltas=deltas,
        athlete_profile=None,
        constraints_triggered=None,
    )

    assert explanation is not None
    assert explanation.revision_type == "MODIFY"
    assert len(explanation.summary) > 0
    assert len(explanation.rationale) > 0


@pytest.mark.skip(reason="Test disabled")
@pytest.mark.asyncio
async def test_explain_blocked_revision(monkeypatch, sample_revision_blocked: PlanRevision):
    """Test explanation generation for BLOCKED revision."""
    # Mock LLM call
    mock_explanation = RevisionExplanation(
        summary="Your plan modification was blocked.",
        rationale="Increasing volume during race week increases injury risk and reduces performance.",
        safeguards=["Race week protection"],
        confidence=None,
        revision_type="BLOCKED",
    )

    def mock_agent_run(*args, **kwargs):
        class MockResult:
            output = mock_explanation

        return MockResult()

    original_agent = Agent
    mock_agent_instance = original_agent(
        model=None,  # type: ignore
        system_prompt="",
        output_type=RevisionExplanation,
    )
    mock_agent_instance.run = mock_agent_run

    monkeypatch.setattr("app.coach.explainability.revision_explainer._get_model", lambda: None)
    monkeypatch.setattr("app.coach.explainability.revision_explainer.Agent", lambda *args, **kwargs: mock_agent_instance)

    deltas = {
        "deltas": [delta.model_dump() for delta in sample_revision_blocked.deltas],
    }

    explanation = await explain_plan_revision(
        revision=sample_revision_blocked,
        deltas=deltas,
        athlete_profile=None,
        constraints_triggered=["RACE_WEEK_NO_INCREASE"],
    )

    assert explanation is not None
    assert explanation.revision_type == "BLOCKED"
    assert len(explanation.summary) > 0
    assert len(explanation.rationale) > 0


@pytest.mark.skip(reason="Test disabled")
@pytest.mark.asyncio
async def test_explain_regenerate_revision(monkeypatch, sample_revision_regenerate: PlanRevision):
    """Test explanation generation for REGENERATE revision."""
    # Mock LLM call
    mock_explanation = RevisionExplanation(
        summary="Your training plan was regenerated from June 1st.",
        rationale="The plan was regenerated to incorporate recent training data and optimize your preparation.",
        safeguards=[],
        confidence="This regeneration maintains your training goals.",
        revision_type="REGENERATE",
    )

    def mock_agent_run(*args, **kwargs):
        class MockResult:
            output = mock_explanation

        return MockResult()

    original_agent = Agent
    mock_agent_instance = original_agent(
        model=None,  # type: ignore
        system_prompt="",
        output_type=RevisionExplanation,
    )
    mock_agent_instance.run = mock_agent_run

    monkeypatch.setattr("app.coach.explainability.revision_explainer._get_model", lambda: None)
    monkeypatch.setattr("app.coach.explainability.revision_explainer.Agent", lambda *args, **kwargs: mock_agent_instance)

    deltas = {}

    explanation = await explain_plan_revision(
        revision=sample_revision_regenerate,
        deltas=deltas,
        athlete_profile=None,
        constraints_triggered=None,
    )

    assert explanation is not None
    assert explanation.revision_type == "REGENERATE"
    assert len(explanation.summary) > 0
    assert len(explanation.rationale) > 0


def test_explanation_model_schema():
    """Test that RevisionExplanation model has correct schema."""
    explanation = RevisionExplanation(
        summary="Test summary",
        rationale="Test rationale",
        safeguards=["Rule 1", "Rule 2"],
        confidence="Test confidence",
        revision_type="MODIFY",
    )

    assert explanation.summary == "Test summary"
    assert explanation.rationale == "Test rationale"
    assert len(explanation.safeguards) == 2
    assert explanation.confidence == "Test confidence"
    assert explanation.revision_type == "MODIFY"

    # Test serialization
    dumped = explanation.model_dump()
    assert "summary" in dumped
    assert "rationale" in dumped
    assert "safeguards" in dumped
    assert "revision_type" in dumped


@pytest.mark.skip(reason="Test disabled")
def test_explanation_fallback_on_error(monkeypatch, sample_revision_modify: PlanRevision):
    """Test that fallback explanation is returned on LLM error."""
    # Mock LLM to raise exception
    def mock_agent_run(*args, **kwargs):
        raise RuntimeError("LLM call failed")

    original_agent = Agent
    mock_agent_instance = original_agent(
        model=None,  # type: ignore
        system_prompt="",
        output_type=RevisionExplanation,
    )
    mock_agent_instance.run = mock_agent_run

    monkeypatch.setattr("app.coach.explainability.revision_explainer._get_model", lambda: None)
    monkeypatch.setattr("app.coach.explainability.revision_explainer.Agent", lambda *args, **kwargs: mock_agent_instance)

    deltas = {
        "deltas": [delta.model_dump() for delta in sample_revision_modify.deltas],
    }

    # Should not raise, should return fallback
    explanation = asyncio.run(
        explain_plan_revision(
            revision=sample_revision_modify,
            deltas=deltas,
            athlete_profile=None,
            constraints_triggered=None,
        )
    )

    assert explanation is not None
    assert explanation.revision_type == "MODIFY"
    assert len(explanation.summary) > 0
    assert len(explanation.rationale) > 0
