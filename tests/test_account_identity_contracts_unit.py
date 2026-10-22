"""Verified identity persistence reports outcomes only after its session scope ends.

Insert/update return values and scope failures are boundaries. These tests do not
simulate SQL conflict arbitration, transaction commit, or activity throttling.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_user_api import accounts
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def persistence(monkeypatch):
    identifier = uuid.uuid4()
    session = Mock(spec=Session)
    events = []

    @contextmanager
    def scope():
        events.append("enter")
        try:
            yield session
        finally:
            events.append("exit")

    monkeypatch.setattr(accounts, "session_factory", lambda: SimpleNamespace(begin=scope))
    monkeypatch.setattr(accounts, "utcnow", lambda: NOW)
    identity = {
        "issuer": "https://identity.example",
        "subject": "reader",
        "organization_id": "readers",
        "name": "Verified provider name",
        "email": "verified@example.test",
        "profile": {"display_name": "Untrusted profile replacement"},
        "username": "forged-name",
    }
    return SimpleNamespace(id=identifier, session=session, events=events, identity=identity)


@pytest.mark.parametrize("created", [False, True])
def test_sign_in_reports_created_or_existing_account_and_preserves_profile_ownership(
    persistence, created
):
    state = persistence
    state.session.scalar.side_effect = [state.id] if created else [None, state.id]

    identifier, is_new = accounts.save_user_with_status(state.identity)

    assert identifier == str(state.id) and is_new is created
    assert state.events == ["enter", "exit"]
    assert state.session.scalar.call_count == (1 if created else 2)
    for call in state.session.scalar.call_args_list:
        parameters = call.args[0].compile().params
        assert parameters["name"] == state.identity["name"]
        assert parameters["email"] == state.identity["email"]
        assert parameters["last_seen_at"] == NOW
        assert parameters.get("profile") != state.identity["profile"]
        assert parameters.get("username") != "forged-name"


def test_account_disappearing_during_sign_in_is_an_explicit_failure(persistence):
    persistence.session.scalar.side_effect = [None, None]

    with pytest.raises(SQLAlchemyError, match="disappeared"):
        accounts.save_user_with_status(persistence.identity)

    assert persistence.events == ["enter", "exit"]


def test_simple_identity_save_returns_the_same_committed_identifier(persistence):
    persistence.session.scalar.return_value = persistence.id

    assert accounts.save_user(persistence.identity) == str(persistence.id)

    assert persistence.events == ["enter", "exit"]


def test_sign_in_scope_failure_cannot_report_a_successful_account_creation(
    persistence, monkeypatch
):
    persistence.session.scalar.return_value = persistence.id

    @contextmanager
    def failed_scope():
        yield persistence.session
        raise SQLAlchemyError("scope failed")

    monkeypatch.setattr(accounts, "session_factory", lambda: SimpleNamespace(begin=failed_scope))

    with pytest.raises(SQLAlchemyError, match="scope failed"):
        accounts.save_user_with_status(persistence.identity)


def test_activity_storage_failure_does_not_break_authenticated_requests_or_log_credentials(
    persistence, caplog
):
    persistence.session.execute.side_effect = SQLAlchemyError("private-database-secret")

    accounts.touch_user_activity(str(persistence.id))

    assert persistence.events == ["enter", "exit"]
    assert "user_activity_update_deferred" in caplog.messages
    assert "private-database-secret" not in caplog.text


def test_activity_success_leaves_the_session_scope_before_returning(persistence):
    accounts.touch_user_activity(str(persistence.id))

    assert persistence.events == ["enter", "exit"]
