"""Partner writes retain live scope and deliberate lock ordering across all workflows."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.models import PartnerAccount, PartnerProduct
from devfeed_partner_api import access, portal
from devfeed_partner_api.auth import PartnerIdentity
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError


def sql(statement):
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


@pytest.fixture
def identity():
    return PartnerIdentity(
        subject="partner-user",
        issuer="https://identity.example",
        organization_id="organization",
        roles=["partner"],
        expires_at=2**31,
        csrf_token="test",
    )


@pytest.fixture
def account():
    return PartnerAccount(id=uuid.uuid4(), name="Account", status="active", tier="silver")


def test_query_scopes_memberships_in_sql_before_pagination(identity):
    session = Mock()
    statement = access.PartnerAccountAccess(session, identity).query()
    scope = sql(statement)
    assert "partner_accounts.status = 'active'" in scope
    assert "partner_memberships.account_id = partner_accounts.id" in scope
    assert identity.issuer in scope and identity.subject in scope
    assert "EXISTS" in scope and "LIMIT" not in scope and "OFFSET" not in scope
    session.scalar.assert_not_called()


def test_explicit_account_filter_requires_current_access(identity, account):
    session = Mock()
    session.scalar.return_value = account
    guard = access.PartnerAccountAccess(session, identity)
    statement = guard.query(account.id)
    assert str(account.id) in sql(statement)
    session.scalar.assert_called_once()
    session.scalar.return_value = None
    with pytest.raises(HTTPException) as error:
        guard.query(account.id)
    assert error.value.status_code == 404
    session.commit.assert_not_called()


@pytest.mark.parametrize("nowait", [False, True])
def test_lock_refreshes_account_then_fences_exact_membership(identity, account, nowait):
    session = Mock()
    session.scalar.side_effect = [account, object()]
    assert access.PartnerAccountAccess(session, identity).lock(account.id, nowait=nowait) is account
    statements = [call.args[0] for call in session.scalar.call_args_list]
    account_query, member_query = map(sql, statements)
    assert account_query.endswith("FOR UPDATE NOWAIT" if nowait else "FOR UPDATE")
    assert member_query.endswith("FOR SHARE")
    assert identity.issuer in member_query and identity.subject in member_query
    assert str(account.id) in member_query
    assert all(statement.get_execution_options()["populate_existing"] for statement in statements)
    session.commit.assert_not_called()


def test_stale_require_does_not_authorize_after_membership_revocation(identity, account):
    session = Mock()
    session.scalar.side_effect = [account, account, None]
    guard = access.PartnerAccountAccess(session, identity)
    assert guard.require(account.id) is account
    with pytest.raises(HTTPException) as error:
        guard.lock(account.id)
    assert error.value.status_code == 404
    session.commit.assert_not_called()


def test_missing_or_inactive_account_is_rejected_before_membership_lock(identity):
    session = Mock()
    session.scalar.return_value = None
    with pytest.raises(HTTPException) as error:
        access.PartnerAccountAccess(session, identity).lock(uuid.uuid4())
    assert error.value.status_code == 404
    session.scalar.assert_called_once()
    session.commit.assert_not_called()


@pytest.mark.parametrize("sqlstate", ["55P03", "40P01"])
def test_nowait_preserves_busy_response_and_unrelated_database_errors(identity, sqlstate):
    cause = RuntimeError("safe database category")
    cause.sqlstate = sqlstate
    failure = DBAPIError(None, None, cause)
    session = Mock()
    session.scalar.side_effect = failure
    guard = access.PartnerAccountAccess(session, identity)
    if sqlstate == "55P03":
        with pytest.raises(HTTPException) as error:
            guard.lock(uuid.uuid4(), nowait=True)
        assert error.value.status_code == 503
        assert error.value.headers == {"Retry-After": "1"}
        session.rollback.assert_called_once()
    else:
        with pytest.raises(DBAPIError) as error:
            guard.lock(uuid.uuid4(), nowait=True)
        assert error.value is failure
        session.rollback.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["product", "manual-id", "manual-url"])
def test_owned_product_workflows_preserve_lock_order(monkeypatch, identity, account, kind):
    session, events = Mock(), []
    product = PartnerProduct(id=uuid.uuid4())

    def read(statement):
        query = sql(statement)
        if query.endswith("FOR SHARE"):
            events.append("membership")
            return object()
        events.append("account" if "FOR UPDATE" in query else "require")
        return account

    session.scalar.side_effect = read
    monkeypatch.setattr(
        access, "lock_image_product", Mock(side_effect=lambda *_: events.append("product"))
    )
    manual = Mock(side_effect=lambda *a, **kw: (events.append("catalog/product"), product)[1])
    monkeypatch.setattr(access, "lock_manual_identity", manual)
    guard = access.PartnerAccountAccess(session, identity)
    if kind == "product":
        assert guard.lock_product(account.id, product.id) is account
        assert events == ["require", "product", "account", "membership"]
        manual.assert_not_called()
    else:
        options = (
            {"product_id": product.id} if kind == "manual-id" else {"url": "https://tool.example/"}
        )
        assert guard.lock_manual(account.id, **options) == (account, product)
        assert events == ["require", "catalog/product", "account", "membership"]
        assert manual.call_args.kwargs == {
            "product_id": options.get("product_id"),
            "url": options.get("url"),
        }
    session.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["product", "manual"])
def test_foreign_account_cannot_lock_catalog_or_product(monkeypatch, identity, kind):
    session = Mock()
    session.scalar.return_value = None
    product = Mock()
    manual = Mock()
    monkeypatch.setattr(access, "lock_image_product", product)
    monkeypatch.setattr(access, "lock_manual_identity", manual)
    guard = access.PartnerAccountAccess(session, identity)
    with pytest.raises(HTTPException) as error:
        if kind == "product":
            guard.lock_product(uuid.uuid4(), uuid.uuid4())
        else:
            guard.lock_manual(uuid.uuid4(), url="https://tool.example/")
    assert error.value.status_code == 404
    product.assert_not_called()
    manual.assert_not_called()
    session.commit.assert_not_called()


def test_preferences_validate_after_membership_guard_and_enroll_before_commit(
    monkeypatch, identity, account
):
    session = Mock()
    session.scalar.side_effect = [account, SimpleNamespace()]
    events = []
    session.flush.side_effect = lambda: events.append("flush")
    session.commit.side_effect = lambda: events.append("commit")
    enrollment = Mock(side_effect=lambda *_: events.append("enroll"))
    monkeypatch.setattr(portal, "request_promotion_enrollment", enrollment)
    result = portal.update_preferences(
        account.id,
        portal.PartnerPreferencesInput(outbound_destination="product_url"),
        session,
        identity,
    )
    assert result is account and account.outbound_destination == "product_url"
    assert sql(session.scalar.call_args_list[-1].args[0]).endswith("FOR SHARE")
    assert events == ["flush", "enroll", "commit"]
    enrollment.assert_called_once_with(session, account.id)
