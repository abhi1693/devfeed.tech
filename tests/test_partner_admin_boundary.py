"""The reporting service exposes no management routes; admin sessions own all writes."""

import json
import time
import uuid
from unittest.mock import Mock

import pytest
from devfeed_admin_api import auth, oidc, partner_accounts
from devfeed_admin_api.config import Settings
from devfeed_admin_api.dependencies import get_session
from devfeed_partner_api import portal
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def admin_boundary(monkeypatch):
    settings = Settings(
        _env_file=None,
        admin_base_url="https://admin.example",
        oidc_issuer_url="https://identity.example",
        oidc_client_id="admin-client",
        oidc_organization_id="org",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    token = "a" * 43
    csrf = "b" * 43
    record = {
        "subject": "admin",
        "issuer": "https://identity.example",
        "organization_id": "org",
        "roles": ["superuser"],
        "expires_at": int(time.time()) + 3600,
        "csrf_token": csrf,
        "policy": oidc.policy_key(settings),
    }
    store = Mock()
    store.get.side_effect = lambda key: (
        json.dumps(record).encode() if key == auth.key("session", token) else None
    )
    monkeypatch.setattr(auth, "get_redis", lambda: store)
    session = Mock()
    session.get.return_value = None
    app = FastAPI()
    app.include_router(partner_accounts.router)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client, session, record, token, csrf


def test_partner_cookie_cannot_authorize_admin_management(admin_boundary):
    client, session, _, token, csrf = admin_boundary
    response = client.post(
        "/v1/admin/partner-accounts",
        json={"name": "Forged", "tier": "Scale"},
        headers={
            "Cookie": f"__Host-devfeed_partner_session={token}",
            "Origin": "https://admin.example",
            "x-csrf-token": csrf,
        },
    )
    assert response.status_code == 401
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "origin,csrf", [("https://partner.example", "b" * 43), ("https://admin.example", "c" * 43)]
)
def test_admin_management_enforces_origin_and_csrf(admin_boundary, origin, csrf):
    client, session, _, token, _ = admin_boundary
    response = client.post(
        "/v1/admin/partner-accounts",
        json={"name": "Forged", "tier": "Scale"},
        headers={
            "Cookie": f"__Host-devfeed_admin_session={token}",
            "Origin": origin,
            "x-csrf-token": csrf,
        },
    )
    assert response.status_code == 403
    session.commit.assert_not_called()


def test_valid_admin_session_can_create_account(admin_boundary):
    client, session, _, token, csrf = admin_boundary

    def add(account):
        account.id = uuid.uuid4()

    session.add.side_effect = add
    response = client.post(
        "/v1/admin/partner-accounts",
        json={"name": "Example", "tier": "Growth"},
        headers={
            "Cookie": f"__Host-devfeed_admin_session={token}",
            "Origin": "https://admin.example",
            "x-csrf-token": csrf,
        },
    )
    assert response.status_code == 201
    session.commit.assert_called_once()


def test_partner_service_has_no_management_endpoints():
    app = FastAPI()
    app.include_router(portal.router)
    paths = app.openapi()["paths"]
    assert set(paths) == {"/v1/partner/accounts", "/v1/partner/accounts/{account_id}/dashboard"}
    assert all(set(operations) == {"get"} for operations in paths.values())
    with TestClient(app) as client:
        assert client.post("/v1/partner/accounts", json={}).status_code == 405
        assert client.get(f"/v1/partner/accounts/{uuid.uuid4()}/members").status_code == 404
