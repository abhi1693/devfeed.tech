"""Tenant boundaries and truthful reporting against a disposable PostgreSQL schema."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDailyMetric,
    PartnerMembership,
    UserAccount,
)
from devfeed_partner_api.auth import PartnerIdentity, require_partner
from devfeed_partner_api.dependencies import get_session
from devfeed_partner_api.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration
ISSUER = "https://identity.example"


@pytest.fixture
def portal_client(database):
    identity = PartnerIdentity(
        subject="alice",
        issuer=ISSUER,
        organization_id="org-1",
        roles=["partner"],
        expires_at=2**31,
        csrf_token="test",
    )
    app = create_app()
    app.dependency_overrides[require_partner] = lambda: identity

    def request_session():
        with database() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    with TestClient(app) as client:
        yield client, identity


@pytest.fixture
def admin_portal_client(database):
    from devfeed_admin_api.auth import AdminIdentity, require_admin
    from devfeed_admin_api.dependencies import get_session as admin_session
    from devfeed_admin_api.partner_accounts import router
    from fastapi import FastAPI

    identity = AdminIdentity(
        subject="admin",
        issuer=ISSUER,
        organization_id="org-1",
        roles=["superuser"],
        expires_at=2**31,
        csrf_token="test",
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: identity

    def request_session():
        with database() as session:
            yield session

    app.dependency_overrides[admin_session] = request_session
    with TestClient(app) as client:
        yield client, identity


def seed(database):
    with database() as session:
        a = PartnerAccount(name="Alpha", tier="gold")
        b = PartnerAccount(name="Beta", tier="bronze")
        session.add_all([a, b])
        session.flush()
        session.add_all(
            [
                PartnerMembership(account_id=a.id, issuer=ISSUER, subject="alice"),
                PartnerMembership(account_id=a.id, issuer=ISSUER, subject="bob"),
                PartnerMembership(account_id=b.id, issuer=ISSUER, subject="carol"),
            ]
        )
        asset_a = PartnerAsset(account_id=a.id, name="Alpha ad", kind="ad", status="active")
        asset_b = PartnerAsset(account_id=b.id, name="Beta ad", kind="ad", status="active")
        session.add_all([asset_a, asset_b])
        session.commit()
        return a.id, b.id, asset_a.id, asset_b.id


def test_multi_user_membership_and_superuser_cannot_bypass_scope(portal_client, database):
    client, identity = portal_client
    a, b, _, _ = seed(database)
    assert [item["id"] for item in client.get("/v1/partner/accounts").json()["items"]] == [str(a)]
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404
    identity.subject = "bob"
    assert client.get(f"/v1/partner/accounts/{a}/dashboard").status_code == 200
    identity.subject = "no-membership"
    assert client.get("/v1/partner/accounts").json()["total"] == 0
    identity.roles = ["superuser"]
    assert client.get("/v1/partner/accounts").json()["total"] == 0
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404
    assert client.get(f"/v1/partner/accounts/{a}/members").status_code == 404


def test_revocation_pause_and_issuer_are_checked_live(portal_client, database):
    client, identity = portal_client
    a, b, _, _ = seed(database)
    with database() as session:
        session.add(PartnerMembership(account_id=b, issuer=ISSUER, subject="alice"))
        session.commit()
    assert client.get("/v1/partner/accounts").json()["total"] == 2
    identity.issuer = "https://other-issuer.example"
    assert client.get("/v1/partner/accounts").json()["total"] == 0
    identity.issuer = ISSUER
    with database() as session:
        session.delete(session.get(PartnerMembership, (a, ISSUER, "alice")))
        session.get(PartnerAccount, b).status = "paused"
        session.commit()
    assert client.get(f"/v1/partner/accounts/{a}/dashboard").status_code == 404
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404
    identity.roles = ["superuser"]
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404


def test_totals_time_windows_and_tenant_filtering(portal_client, database):
    client, identity = portal_client
    a, b, asset_a, asset_b = seed(database)
    today = datetime.now(UTC).date()
    with database() as session:
        session.add_all(
            [
                PartnerDailyMetric(asset_id=asset_a, day=today, impressions=100, clicks=5),
                PartnerDailyMetric(
                    asset_id=asset_a, day=today - timedelta(days=7), impressions=1000, clicks=20
                ),
                PartnerDailyMetric(asset_id=asset_b, day=today, impressions=99999, clicks=9000),
            ]
        )
        session.commit()
    result = client.get(f"/v1/partner/accounts/{a}/dashboard?days=7").json()
    assert result["totals"] == {"impressions": 100, "clicks": 5, "ctr": 5.0, "measured_days": 1}
    assert len(result["trend"]) == 1
    assert result["assets"][0]["id"] == str(asset_a)
    assert (
        client.get(f"/v1/partner/accounts/{a}/dashboard?days=30").json()["totals"]["impressions"]
        == 1100
    )
    # Summary remains global to the account even when asset pagination is empty.
    page = client.get(f"/v1/partner/accounts/{a}/dashboard?days=7&offset=1").json()
    assert page["assets"] == []
    assert page["totals"]["clicks"] == 5
    identity.roles = ["superuser"]
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404


def test_empty_zero_metrics_and_idempotent_trusted_import(
    portal_client, admin_portal_client, database
):
    client, identity = portal_client
    admin, _ = admin_portal_client
    a, b, asset_a, asset_b = seed(database)
    empty = client.get(f"/v1/partner/accounts/{a}/dashboard").json()
    assert empty["totals"]["measured_days"] == 0
    assert empty["totals"]["ctr"] is None
    identity.roles = ["superuser"]
    url = f"/v1/admin/partner-accounts/{a}/assets/{asset_a}/metrics"
    payload = {"day": datetime.now(UTC).date().isoformat(), "impressions": 0, "clicks": 0}
    for _ in range(2):
        assert admin.put(url, json=payload).status_code == 204
    measured = client.get(f"/v1/partner/accounts/{a}/dashboard").json()
    assert measured["totals"] == {"impressions": 0, "clicks": 0, "ctr": None, "measured_days": 1}
    assert admin.put(url, json={**payload, "impressions": -1}).status_code == 422
    assert (
        admin.put(
            url, json={**payload, "day": (datetime.now(UTC).date() + timedelta(days=1)).isoformat()}
        ).status_code
        == 422
    )
    # Even a superuser cannot address another account's asset under this path.
    assert (
        admin.put(
            f"/v1/admin/partner-accounts/{a}/assets/{asset_b}/metrics", json=payload
        ).status_code
        == 404
    )


def test_management_is_superuser_only_and_membership_is_idempotent(admin_portal_client, database):
    client, identity = admin_portal_client
    identity.roles = ["partner"]
    a, b, asset_a, _ = seed(database)
    assert (
        client.put(
            f"/v1/admin/partner-accounts/{a}/members", json={"subject": "mallory"}
        ).status_code
        == 403
    )
    assert client.get(f"/v1/admin/partner-accounts/{a}/members").status_code == 403
    assert (
        client.post(
            "/v1/admin/partner-accounts", json={"name": "Gamma", "tier": "silver"}
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/v1/admin/partner-accounts/{a}/assets/{asset_a}/metrics",
            json={"day": datetime.now(UTC).date().isoformat(), "impressions": 500, "clicks": 30},
        ).status_code
        == 403
    )
    identity.roles = ["superuser"]
    with database() as session:
        user = UserAccount(
            issuer=ISSUER,
            subject="new-user",
            organization_id=identity.organization_id,
            name="New partner",
        )
        session.add(user)
        session.commit()
        user_id = str(user.id)
    for _ in range(2):
        assert (
            client.put(
                f"/v1/admin/partner-accounts/{a}/members", json={"user_id": user_id}
            ).status_code
            == 204
        )
    assert (
        client.put(
            f"/v1/admin/partner-accounts/{a}/members", json={"subject": "email@example.com"}
        ).status_code
        == 422
    )
    assert len(client.get(f"/v1/admin/partner-accounts/{a}/members").json()) == 3
    assert client.delete(f"/v1/admin/partner-accounts/{a}/members/new-user").status_code == 204
    assert len(client.get(f"/v1/admin/partner-accounts/{a}/members").json()) == 2
    created = client.post("/v1/admin/partner-accounts", json={"name": "Gamma", "tier": "silver"})
    assert created.status_code == 201
    assert (
        client.post(
            "/v1/admin/partner-accounts", json={"name": "Invalid tier", "tier": "custom"}
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/v1/admin/partner-accounts/{a}",
            json={"name": "Alpha", "tier": "gold", "benefits": ["Forged benefit"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/admin/partner-accounts/{a}/assets", json={"name": "Invalid", "kind": "product"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/admin/partner-accounts/{a}/assets",
            json={"name": "Invalid", "kind": "product", "product_id": str(uuid.uuid4())},
        ).status_code
        == 422
    )
    assert client.get("/v1/admin/partner-accounts?limit=1&offset=1").json()["total"] == 3
    assert len(client.get("/v1/admin/partner-accounts?limit=1&offset=1").json()["items"]) == 1
    assert client.get(f"/v1/admin/partner-accounts/{a}/dashboard?days=366").status_code == 422


def test_shared_catalog_product_keeps_partner_reporting_separate(
    portal_client, admin_portal_client, database
):
    from devfeed_core.models import PartnerProduct

    client, identity = portal_client
    admin, _ = admin_portal_client
    a, b, _, _ = seed(database)
    with database() as session:
        product = PartnerProduct(
            name="Shared product",
            product_url="https://product.example",
            description="Developer tool",
        )
        session.add(product)
        session.commit()
        product_id = str(product.id)
    identity.roles = ["superuser"]
    payload = {
        "name": "Shared product placement",
        "kind": "product",
        "product_id": product_id,
        "status": "active",
    }
    alpha = admin.post(f"/v1/admin/partner-accounts/{a}/assets", json=payload)
    beta = admin.post(f"/v1/admin/partner-accounts/{b}/assets", json=payload)
    assert alpha.status_code == beta.status_code == 201
    alpha_id, beta_id = alpha.json()["id"], beta.json()["id"]
    assert alpha_id != beta_id
    assert (
        admin.put(
            f"/v1/admin/partner-accounts/{a}/assets/{alpha_id}",
            json={**payload, "status": "paused"},
        ).json()["status"]
        == "paused"
    )
    assert (
        admin.put(
            f"/v1/admin/partner-accounts/{a}",
            json={"name": "Alpha", "tier": "diamond"},
        ).json()["tier"]
        == "diamond"
    )
    for account_id, asset_id, impressions in ((a, alpha_id, 100), (b, beta_id, 900)):
        assert (
            admin.put(
                f"/v1/admin/partner-accounts/{account_id}/assets/{asset_id}/metrics",
                json={
                    "day": datetime.now(UTC).date().isoformat(),
                    "impressions": impressions,
                    "clicks": 10,
                },
            ).status_code
            == 204
        )
    identity.roles = ["partner"]
    data = client.get(f"/v1/partner/accounts/{a}/dashboard").json()
    assert data["totals"]["impressions"] == 100
    assert data["account"]["tier"] == "diamond"
    from devfeed_core.partner_tiers import TIER_BENEFITS

    assert data["account"]["benefits"] == list(TIER_BENEFITS["diamond"])
    assert {asset["id"] for asset in data["assets"]}.isdisjoint({beta_id})
    assert (
        client.put(
            f"/v1/partner/accounts/{a}", json={"name": "Alpha", "tier": "Forged"}
        ).status_code
        == 404
    )


def test_member_user_picker_lists_only_current_identity_organization(admin_portal_client, database):
    from devfeed_admin_api.users import router as users_router

    client, identity = admin_portal_client
    client.app.include_router(users_router)
    with database() as session:
        session.add_all(
            [
                UserAccount(
                    issuer=identity.issuer,
                    organization_id=identity.organization_id,
                    subject="known",
                    name="Picker allowed",
                ),
                UserAccount(
                    issuer="https://foreign.example",
                    organization_id=identity.organization_id,
                    subject="foreign",
                    name="Picker foreign issuer",
                ),
                UserAccount(
                    issuer=identity.issuer,
                    organization_id="foreign-org",
                    subject="foreign-org",
                    name="Picker foreign organization",
                ),
            ]
        )
        session.commit()
    response = client.get("/v1/admin/users?identity_only=true&q=Picker")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "Picker allowed"
    assert client.get("/v1/admin/users?q=Picker").json()["total"] == 3
