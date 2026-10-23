"""Account settings reads do not depend on reporting or membership queries."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api.auth import AdminIdentity, require_admin
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.partner_accounts import router
from devfeed_core.models import PartnerAccount
from devfeed_http.partner_portal import AccountInput
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def account_client():
    identifier = uuid.uuid4()
    record = PartnerAccount(
        id=identifier, **AccountInput(name="Account policy", tier="gold").model_dump()
    )
    session = Mock()
    session.scalar.return_value = record
    identity = AdminIdentity(
        subject="admin",
        issuer="https://identity.example",
        organization_id="org",
        roles=["superuser"],
        expires_at=2**31,
        csrf_token="test",
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_admin] = lambda: identity
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            session=session,
            record=record,
            identity=identity,
            path=f"/v1/admin/partner-accounts/{identifier}",
        )


def test_account_detail_returns_policy_using_one_account_read_without_reports_or_members(
    account_client,
):
    state = account_client
    response = state.client.get(state.path)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(state.record.id) and body["name"] == "Account policy"
    assert body["tier"] == "gold" and "search" in body["promotion_surfaces"]
    assert body["allowed_ad_formats"] and body["benefits"]
    assert not {"assets", "asset_total", "totals", "trend", "members"}.intersection(body)
    state.session.scalar.assert_called_once()
    statement = state.session.scalar.call_args.args[0]
    assert [relation.name for relation in statement.get_final_froms()] == ["partner_accounts"]
    assert state.record.id in statement.compile().params.values()
    assert statement._for_update_arg is None
    state.session.execute.assert_not_called()
    state.session.scalars.assert_not_called()


def test_account_detail_distinguishes_missing_and_malformed_identifiers(account_client):
    state = account_client
    state.session.scalar.return_value = None
    missing = state.client.get(state.path)
    assert missing.status_code == 404 and missing.json()["detail"] == "Partner account not found"
    state.session.reset_mock()
    assert state.client.get("/v1/admin/partner-accounts/not-a-uuid").status_code == 422
    state.session.scalar.assert_not_called()


@pytest.mark.parametrize("status", [401, 403])
def test_account_detail_keeps_authentication_and_superuser_boundary(account_client, status):
    state = account_client
    if status == 401:

        def unauthenticated():
            raise HTTPException(401, "Authentication required")

        state.app.dependency_overrides[require_admin] = unauthenticated
    else:
        state.identity.roles = ["admin"]
    assert state.client.get(state.path).status_code == status
    state.session.scalar.assert_not_called()


def test_static_account_catalog_routes_keep_precedence_over_detail_identifiers(account_client):
    state = account_client
    response = state.client.get("/v1/admin/partner-accounts/tiers")
    assert response.status_code == 200 and len(response.json()) == 5
    state.session.scalar.assert_not_called()
    paths = [route.path for route in router.routes]
    detail = paths.index("/v1/admin/partner-accounts/{account_id}")
    assert all(
        paths.index(f"/v1/admin/partner-accounts/{name}") < detail
        for name in ("tiers", "audience-topics", "sponsor-articles")
    )
