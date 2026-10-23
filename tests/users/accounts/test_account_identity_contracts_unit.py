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
from devfeed_core import identity_accounts
from devfeed_core.models import UserAccount
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


@pytest.mark.parametrize("optional", [False, True])
def test_verified_identity_requests_only_provider_fields_without_replacing_profile_or_memberships(
    monkeypatch, optional
):
    from tests.support.source_readiness import NOW

    identifier, session, phases = uuid.uuid4(), Mock(spec=Session), []
    session.scalar.return_value = identifier

    @contextmanager
    def scope():
        phases.append("enter")
        yield session
        phases.append("exit")

    monkeypatch.setattr(identity_accounts, "session_factory", lambda: SimpleNamespace(begin=scope))
    monkeypatch.setattr(identity_accounts, "utcnow", lambda: NOW)
    verified = {
        "issuer": "https://identity.example",
        "subject": "verified-partner",
        "organization_id": "partners",
        "name": "Provider name" if optional else None,
        "email": "verified@example.test" if optional else None,
        "profile": {"display_name": "Provider cannot replace reader settings"},
        "username": "provider-cannot-claim-this",
        "memberships": [str(uuid.uuid4())],
    }

    assert identity_accounts.register_verified_user(verified) == str(identifier)
    assert phases == ["enter", "exit"]
    session.scalar.assert_called_once()
    statement = session.scalar.call_args.args[0]
    assert statement.table.name == UserAccount.__tablename__
    expected = {
        **{
            name: verified[name]
            for name in ("issuer", "subject", "organization_id", "name", "email")
        },
        "last_seen_at": NOW,
    }
    parameters = statement.compile().params
    assert {name: parameters[name] for name in expected} == expected
    assert parameters.get("profile") != verified["profile"]
    assert parameters.get("username") != verified["username"]
    assert "memberships" not in parameters
    session.add.assert_not_called()
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("failure", ["write", "scope-exit"])
def test_identity_persistence_failure_cannot_return_a_successful_account_identifier(
    monkeypatch, failure
):
    session, phases = Mock(spec=Session), []
    session.scalar.return_value = uuid.uuid4()
    if failure == "write":
        session.scalar.side_effect = SQLAlchemyError("identity write failed")

    @contextmanager
    def scope():
        phases.append("enter")
        try:
            yield session
            if failure == "scope-exit":
                raise SQLAlchemyError("identity scope failed")
        finally:
            phases.append("exit")

    monkeypatch.setattr(identity_accounts, "session_factory", lambda: SimpleNamespace(begin=scope))
    with pytest.raises(SQLAlchemyError, match="identity"):
        identity_accounts.register_verified_user(
            {
                "issuer": "https://identity.example",
                "subject": "partner",
                "organization_id": "partners",
            }
        )
    assert phases == ["enter", "exit"]
