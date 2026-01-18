"""Phase 6 — Knowledge Grounding Tests.

Tests that RAG integration is explanatory only and does not influence decisions.
"""

import pytest

from app.explanations.rationale import generate_rationale
from app.tools.read.knowledge import query_coaching_knowledge
from app.tools.read.rationale import generate_plan_rationale
from app.tools.read.recommendations import recommend_no_change


def test_rag_is_explanatory_only():
    """Test that RAG does not alter recommendation."""
    rec = recommend_no_change("Stable metrics")

    # Get knowledge snippets
    knowledge = query_coaching_knowledge("fatigue", k=3)

    # Generate rationale with knowledge
    rationale = generate_plan_rationale(
        context={"user_id": "test"},
        compliance={"completion_pct": 0.8},
        trends={"direction": "flat"},
        risks=[],
        recommendation=rec,
        knowledge=knowledge,
    )

    # Recommendation should be unchanged
    assert rationale["recommendation"] == rec
    assert rationale["recommendation"]["recommendation"] == "no_change"


def test_knowledge_query_returns_list():
    """Test that query_coaching_knowledge returns a list."""
    snippets = query_coaching_knowledge("tapering", k=3)

    assert isinstance(snippets, list)
    # May be empty if RAG artifacts not available, but should not raise


def test_knowledge_snippets_format():
    """Test that knowledge snippets have expected format."""
    snippets = query_coaching_knowledge("progression", k=2)

    for snippet in snippets:
        assert isinstance(snippet, dict)
        # All fields are optional, but if present should be correct type
        if "id" in snippet:
            assert isinstance(snippet["id"], str)
        if "title" in snippet:
            assert isinstance(snippet["title"], str)
        if "excerpt" in snippet:
            assert isinstance(snippet["excerpt"], str)
        if "source" in snippet:
            assert isinstance(snippet["source"], str)


def test_rationale_includes_background_with_knowledge():
    """Test that rationale includes background when knowledge is provided."""
    rec = recommend_no_change("Stable")
    knowledge = [
        {
            "id": "test-1",
            "title": "Test Knowledge",
            "excerpt": "Test excerpt",
            "source": "internal",
        }
    ]

    rationale = generate_rationale(
        _context={},
        compliance={"completion_pct": 0.8},
        trends={"direction": "flat"},
        risks=[],
        recommendation=rec,
        knowledge=knowledge,
    )

    assert "background" in rationale
    assert isinstance(rationale["background"], list)
    assert len(rationale["background"]) == 1
    assert rationale["background"][0]["title"] == "Test Knowledge"


def test_rationale_no_background_without_knowledge():
    """Test that rationale does not include background when knowledge is None."""
    rec = recommend_no_change("Stable")

    rationale = generate_rationale(
        _context={},
        compliance={"completion_pct": 0.8},
        trends={"direction": "flat"},
        risks=[],
        recommendation=rec,
        knowledge=None,
    )

    assert "background" not in rationale


def test_rationale_handles_empty_knowledge():
    """Test that rationale handles empty knowledge list gracefully."""
    rec = recommend_no_change("Stable")

    rationale = generate_rationale(
        _context={},
        compliance={"completion_pct": 0.8},
        trends={"direction": "flat"},
        risks=[],
        recommendation=rec,
        knowledge=[],
    )

    # Should not include background for empty list
    assert "background" not in rationale


def test_knowledge_query_never_raises():
    """Test that knowledge query never raises, even on failure."""
    # Should not raise even with invalid inputs
    snippets = query_coaching_knowledge("", k=0)
    assert isinstance(snippets, list)

    snippets = query_coaching_knowledge("invalid_topic_xyz", k=10)
    assert isinstance(snippets, list)


def test_system_works_if_rag_is_empty():
    """Test that system works correctly if RAG returns empty results."""
    # Query with topic that might not exist
    knowledge = query_coaching_knowledge("nonexistent_topic_xyz", k=5)

    # Should return empty list, not None
    assert knowledge == []

    # Rationale should still work
    rec = recommend_no_change("Test")
    rationale = generate_plan_rationale(
        context={},
        compliance={"completion_pct": 0.8},
        trends={"direction": "flat"},
        risks=[],
        recommendation=rec,
        knowledge=knowledge,
    )

    # Should generate rationale successfully
    assert "summary" in rationale
    assert rationale["recommendation"] == rec
