"""Commercial management remains admin-only; scoped partners can edit click preference."""

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

from tests.support.oidc_session_support import provider_session


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
        "provider_session": provider_session(settings, "admin"),
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
        json={"name": "Forged", "tier": "diamond"},
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
        json={"name": "Forged", "tier": "diamond"},
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
        json={"name": "Example", "tier": "gold"},
        headers={
            "Cookie": f"__Host-devfeed_admin_session={token}",
            "Origin": "https://admin.example",
            "x-csrf-token": csrf,
        },
    )
    assert response.status_code == 201
    assert response.json()["link_rel"] == ["nofollow"]
    assert response.json()["configurable_link_rel"] is False
    session.commit.assert_called_once()


def test_partner_service_only_exposes_scoped_product_marketing_and_image_writes():
    app = FastAPI()
    app.include_router(portal.router)
    paths = app.openapi()["paths"]
    preference_path = "/v1/partner/accounts/{account_id}/preferences"
    image_path = "/v1/partner/accounts/{account_id}/products/{product_id}/ad-image"
    products_path = "/v1/partner/accounts/{account_id}/products"
    product_path = f"{products_path}/{{product_id}}"
    assert set(paths) == {
        "/v1/partner/accounts",
        "/v1/partner/accounts/{account_id}",
        "/v1/partner/accounts/{account_id}/dashboard",
        "/v1/partner/accounts/{account_id}/destination-preview",
        "/v1/partner/accounts/{account_id}/audience-topics",
        "/v1/partner/accounts/{account_id}/creative-preview",
        "/v1/partner/accounts/{account_id}/inventory",
        "/v1/partner/accounts/{account_id}/products/{product_id}/promotion",
        products_path,
        product_path,
        image_path,
        preference_path,
    }
    account_get = paths["/v1/partner/accounts/{account_id}"]["get"]
    assert account_get["operationId"] == "partnerAccountGet"
    assert account_get["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AccountOut"
    }
    assert set(paths[preference_path]) == {"put"}
    schema = app.openapi()["components"]["schemas"]["PartnerPreferencesInput"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "outbound_destination",
        "audience",
        "ad_formats",
        "link_rel",
    }
    assert all(
        set(operations) == {"get"}
        for path, operations in paths.items()
        if path
        not in {
            preference_path,
            image_path,
            products_path,
            product_path,
            "/v1/partner/accounts/{account_id}/products/{product_id}/promotion",
        }
    )
    assert set(paths["/v1/partner/accounts/{account_id}/products/{product_id}/promotion"]) == {
        "get",
        "put",
    }
    assert set(paths[image_path]) == {"post"}
    assert set(paths[products_path]) == {"post"}
    assert set(paths[product_path]) == {"get", "patch", "delete"}
    for path, method, model in (
        (products_path, "post", "ManualProductInput"),
        (product_path, "patch", "ManualProductUpdate"),
    ):
        assert paths[path][method]["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}"
        }
        product_schema = app.openapi()["components"]["schemas"][model]
        assert product_schema["additionalProperties"] is False
        assert not {"status", "technologies", "commercial_approved", "tier"}.intersection(
            product_schema["properties"]
        )
    assert "content" not in paths[product_path]["delete"]["responses"]["204"]
    assert paths[image_path]["post"]["operationId"] == "partnerProductAdImageUpload"
    assert set(paths[image_path]["post"]["requestBody"]["content"]) == {"multipart/form-data"}
    assert paths[image_path]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/ProductPromotionOut"}
    with TestClient(app) as client:
        assert client.post("/v1/partner/accounts", json={}).status_code == 405
        assert client.get(f"/v1/partner/accounts/{uuid.uuid4()}/members").status_code == 404


@pytest.mark.parametrize("roles,status", [(["superuser"], 200), (["admin"], 403)])
def test_tier_catalog_requires_superuser_and_returns_code_benefits(admin_boundary, roles, status):
    from devfeed_core.partner_tiers import TIER_BENEFITS, tier_policy

    client, session, record, token, _ = admin_boundary
    record["roles"] = roles
    response = client.get(
        "/v1/admin/partner-accounts/tiers",
        headers={"Cookie": f"__Host-devfeed_admin_session={token}"},
    )
    assert response.status_code == status
    if status == 200:
        assert response.json() == [
            {
                "tier": tier,
                "benefits": list(benefits),
                "link_rel_options": ["nofollow", "sponsored", "ugc", "external"],
                **{
                    key: list(value) if isinstance(value, tuple) else value
                    for key, value in tier_policy(tier).items()
                },
            }
            for tier, benefits in TIER_BENEFITS.items()
        ]
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "issuer,organization,status",
    [
        ("https://identity.example", "org", 204),
        ("https://foreign.example", "org", 404),
        ("https://identity.example", "foreign-org", 404),
    ],
)
def test_member_picker_resolves_scoped_user_identity(admin_boundary, issuer, organization, status):
    from devfeed_core.models import UserAccount

    client, session, _, token, csrf = admin_boundary
    user_id = uuid.uuid4()
    session.get.return_value = UserAccount(
        id=user_id, issuer=issuer, subject="bob", organization_id=organization
    )
    response = client.put(
        f"/v1/admin/partner-accounts/{uuid.uuid4()}/members",
        json={"user_id": str(user_id)},
        headers={
            "Cookie": f"__Host-devfeed_admin_session={token}",
            "Origin": "https://admin.example",
            "x-csrf-token": csrf,
        },
    )
    assert response.status_code == status
    if status == 204:
        session.commit.assert_called_once()
        statement = session.execute.call_args.args[0].compile()
        assert statement.params["subject"] == "bob"
        assert statement.params["issuer"] == "https://identity.example"
    else:
        session.commit.assert_not_called()
