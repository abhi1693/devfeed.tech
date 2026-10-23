"""Tenant boundaries and truthful reporting against a disposable PostgreSQL schema."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerDailyMetric,
    PartnerMembership,
    UserAccount,
)

from tests.support.partner_portal import (
    ISSUER as ISSUER,
)
from tests.support.partner_portal import (
    admin_portal_client as admin_portal_client,
)
from tests.support.partner_portal import (
    portal_client as portal_client,
)
from tests.support.partner_portal import (
    seed as seed,
)

pytestmark = pytest.mark.integration


def test_multi_user_membership_and_superuser_cannot_bypass_scope(portal_client, database):
    client, identity = portal_client
    a, b, _, _ = seed(database)
    assert [item["id"] for item in client.get("/v1/partner/accounts").json()["items"]] == [str(a)]
    assert client.get(f"/v1/partner/accounts/{a}").status_code == 200
    foreign = client.get(f"/v1/partner/accounts/{b}")
    missing = client.get(f"/v1/partner/accounts/{uuid.uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "Partner account not found"}
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404
    identity.subject = "bob"
    assert client.get(f"/v1/partner/accounts/{a}").status_code == 200
    assert client.get(f"/v1/partner/accounts/{a}/dashboard").status_code == 200
    identity.subject = "no-membership"
    assert client.get("/v1/partner/accounts").json()["total"] == 0
    assert client.get(f"/v1/partner/accounts/{a}").status_code == 404
    identity.roles = ["superuser"]
    assert client.get("/v1/partner/accounts").json()["total"] == 0
    assert client.get(f"/v1/partner/accounts/{b}").status_code == 404
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404
    assert client.get(f"/v1/partner/accounts/{a}/members").status_code == 404


def test_revocation_pause_and_issuer_are_checked_live(portal_client, database):
    client, identity = portal_client
    a, b, _, _ = seed(database)
    with database() as session:
        session.add(PartnerMembership(account_id=b, issuer=ISSUER, subject="alice"))
        session.commit()
    assert client.get("/v1/partner/accounts").json()["total"] == 2
    assert client.get(f"/v1/partner/accounts/{a}").status_code == 200
    assert client.get(f"/v1/partner/accounts/{b}").status_code == 200
    identity.issuer = "https://other-issuer.example"
    assert client.get("/v1/partner/accounts").json()["total"] == 0
    assert client.get(f"/v1/partner/accounts/{a}").status_code == 404
    identity.issuer = ISSUER
    with database() as session:
        session.delete(session.get(PartnerMembership, (a, ISSUER, "alice")))
        session.get(PartnerAccount, b).status = "paused"
        session.commit()
    assert client.get(f"/v1/partner/accounts/{a}").status_code == 404
    assert client.get(f"/v1/partner/accounts/{b}").status_code == 404
    assert client.get(f"/v1/partner/accounts/{a}/dashboard").status_code == 404
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404
    identity.roles = ["superuser"]
    assert client.get(f"/v1/partner/accounts/{b}").status_code == 404
    assert client.get(f"/v1/partner/accounts/{b}/dashboard").status_code == 404


def test_account_get_is_independent_of_bounded_picker_pages_and_reordering(portal_client, database):
    client, identity = portal_client
    with database.begin() as session:
        accounts = [
            PartnerAccount(name=f"Member account {index:03}", tier="silver") for index in range(101)
        ]
        session.add_all(accounts)
        session.flush()
        session.add_all(
            PartnerMembership(
                account_id=account.id, issuer=identity.issuer, subject=identity.subject
            )
            for account in accounts
        )
        account_id = accounts[-1].id

    page = client.get("/v1/partner/accounts").json()
    assert page["total"] == 101 and len(page["items"]) == 100
    assert str(account_id) not in {account["id"] for account in page["items"]}
    response = client.get(f"/v1/partner/accounts/{account_id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(account_id)
    assert response.json()["name"] == "Member account 100"

    with database.begin() as session:
        session.get(PartnerAccount, account_id).name = "000 Reordered account"
    later_page = client.get("/v1/partner/accounts?offset=100").json()
    assert later_page["total"] == 101 and len(later_page["items"]) == 1
    assert str(account_id) not in {account["id"] for account in later_page["items"]}
    response = client.get(f"/v1/partner/accounts/{str(account_id).upper()}")
    assert response.status_code == 200
    assert response.json()["id"] == str(account_id)
    assert response.json()["name"] == "000 Reordered account"


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
            tagline="Developer tool",
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
        == 405
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


def test_partner_registration_makes_user_selectable_without_reader_login(
    admin_portal_client, database
):
    from devfeed_admin_api.users import router as users_router
    from devfeed_core.identity_accounts import register_verified_user

    client, admin = admin_portal_client
    client.app.include_router(users_router)
    identity = {
        "issuer": admin.issuer,
        "organization_id": admin.organization_id,
        "subject": "partner-only",
        "name": "Partner only",
        "email": "partner@example.test",
    }
    identifier = register_verified_user(identity)
    with database() as session:
        session.get(UserAccount, uuid.UUID(identifier)).profile = {"bio": "Preserve this profile"}
        session.commit()
    assert register_verified_user({**identity, "name": "Updated partner"}) == identifier
    with database() as session:
        assert session.get(UserAccount, uuid.UUID(identifier)).profile == {
            "bio": "Preserve this profile"
        }
    response = client.get("/v1/admin/users?identity_only=true&q=Updated")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == identifier
    a, _, _, _ = seed(database)
    assert (
        client.put(
            f"/v1/admin/partner-accounts/{a}/members", json={"user_id": identifier}
        ).status_code
        == 204
    )
    assert any(
        member["subject"] == "partner-only"
        for member in client.get(f"/v1/admin/partner-accounts/{a}/members").json()
    )


def test_member_details_join_verified_identity_and_keep_unknown_members(
    admin_portal_client, database
):
    client, admin = admin_portal_client
    account_id, _, _, _ = seed(database)
    with database() as session:
        user = UserAccount(
            issuer=ISSUER,
            subject="alice",
            organization_id=admin.organization_id,
            name="Alice Partner",
            email="alice@example.test",
        )
        session.add(user)
        session.add(
            UserAccount(
                issuer="https://other.example",
                subject="bob",
                organization_id=admin.organization_id,
                name="Wrong issuer",
                email="wrong@example.test",
            )
        )
        session.add(
            UserAccount(
                issuer=ISSUER,
                subject="bob",
                organization_id="other-org",
                name="Wrong organization",
                email="other@example.test",
            )
        )
        session.commit()
        user_id = str(user.id)
    response = client.get(f"/v1/admin/partner-accounts/{account_id}/members")
    assert response.status_code == 200
    members = {member["subject"]: member for member in response.json()}
    assert members["alice"]["user_id"] == user_id
    assert members["alice"]["name"] == "Alice Partner"
    assert members["alice"]["email"] == "alice@example.test"
    assert members["bob"]["user_id"] is None
    assert members["bob"]["name"] is None
    assert members["bob"]["email"] is None
