"""Authorization gate tests (Phase B).

Safety lock: mutation fails without approval. State persisted in conversation_progress.
"""

from unittest.mock import patch

import pytest

from app.coach.flows.authorization import (
    clear_authorization_state,
    get_authorization_state,
    parse_authorization_response,
    require_authorization,
    set_authorization_state,
)


def test_parse_approval():
    """yes / approve / proceed -> approved."""
    assert parse_authorization_response("yes") == "approved"
    assert parse_authorization_response("approve") == "approved"
    assert parse_authorization_response("proceed") == "approved"
    assert parse_authorization_response("  OK  ") == "approved"
    assert parse_authorization_response("go ahead") == "approved"


def test_parse_rejection():
    """no / cancel -> rejected."""
    assert parse_authorization_response("no") == "rejected"
    assert parse_authorization_response("cancel") == "rejected"
    assert parse_authorization_response("abort") == "rejected"
    assert parse_authorization_response("  n  ") == "rejected"


def test_parse_pending():
    """Unclear input -> pending."""
    assert parse_authorization_response("perhaps") == "pending"
    assert parse_authorization_response("tell me more") == "pending"
    assert parse_authorization_response("") == "pending"


def test_require_authorization_fails_when_pending():
    """Mutation intent fails when authorization = pending."""
    with patch("app.coach.flows.authorization.get_authorization_state", return_value="pending"):
        with pytest.raises(RuntimeError, match="requires authorization"):
            require_authorization("c_test-conv", "plan")


def test_require_authorization_fails_when_rejected():
    """Mutation intent fails when authorization = rejected."""
    with patch("app.coach.flows.authorization.get_authorization_state", return_value="rejected"):
        with pytest.raises(RuntimeError, match="requires authorization"):
            require_authorization("c_test-conv", "modify")


def test_require_authorization_fails_when_none():
    """Mutation intent fails when authorization = none."""
    with patch("app.coach.flows.authorization.get_authorization_state", return_value="none"):
        with pytest.raises(RuntimeError, match="requires authorization"):
            require_authorization("c_test-conv", "plan")


def test_require_authorization_proceeds_when_approved():
    """Mutation intent proceeds only when authorization = approved."""
    with patch("app.coach.flows.authorization.get_authorization_state", return_value="approved"):
        require_authorization("c_test-conv", "plan")


def test_authorization_state_persisted_in_conversation_progress():
    """Authorization state is persisted via conversation_progress."""
    create_calls = []
    progress_slots = {}

    def fake_get(cid):
        return type("Progress", (), {"slots": progress_slots})()

    def fake_create(conversation_id, *, slots=None, user_id=None, clear_on_intent_change=True):
        create_calls.append(("create", conversation_id, slots, user_id))
        if slots is not None:
            progress_slots.clear()
            progress_slots.update(slots)

    with (
        patch("app.coach.flows.authorization.get_conversation_progress", side_effect=fake_get),
        patch("app.coach.flows.authorization.create_or_update_progress", side_effect=fake_create),
    ):
        set_authorization_state("c_abc", "approved", user_id="u1")

    assert any(c[0] == "create" and c[1] == "c_abc" and c[3] == "u1" for c in create_calls)
    assert progress_slots.get("authorization_state") == "approved"
    assert "authorization_timestamp" in progress_slots
