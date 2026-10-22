"""Verified partner identity persistence retains user-owned fields and membership authority.

Returned primary keys and scope completion are repository boundaries. Compiled
write values describe requested identity updates, not SQL conflict arbitration or
transaction correctness.
"""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import identity_accounts
from devfeed_core.models import UserAccount
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_source_readiness_unit import NOW


@pytest.mark.parametrize("optional", [False, True])
def test_verified_identity_requests_only_provider_fields_without_replacing_profile_or_memberships(
    monkeypatch, optional
):
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
