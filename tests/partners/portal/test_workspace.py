"""User-wide views cover every current membership before filtering and pagination."""

import uuid

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerConnection,
    PartnerListing,
    PartnerMembership,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductPromotion,
    utcnow,
)

from tests.support.partner_portal import ISSUER
from tests.support.partner_portal import portal_client as portal_client

pytestmark = pytest.mark.integration


def seed_workspace(database, partner_analytics, *, count=2):
    with database.begin() as session:
        accounts = [
            PartnerAccount(name=f"Account {index:03}", tier="silver") for index in range(count)
        ]
        private = PartnerAccount(name="Private", tier="gold")
        paused = PartnerAccount(name="Paused", tier="gold", status="paused")
        session.add_all([*accounts, private, paused])
        session.flush()
        session.add_all(
            PartnerMembership(account_id=account.id, issuer=ISSUER, subject="alice")
            for account in [*accounts, paused]
        )
        product = PartnerProduct(
            name="Shared API checker",
            product_url="https://checker.example",
            tagline="Check OpenAPI compatibility.",
            status="approved",
            verified_at=utcnow(),
        )
        session.add(product)
        session.flush()
        now = utcnow()
        for index, account in enumerate([*accounts, private, paused]):
            provider = f"catalog-{index:03}"
            session.add(
                PartnerConnection(
                    provider=provider,
                    name=f"Catalog {index:03}",
                    account_id=account.id,
                    enabled=True,
                )
            )
            session.flush()
            session.add(
                PartnerListing(
                    provider=provider,
                    product_id=product.id,
                    external_id="checker",
                    name=product.name,
                    product_url=product.product_url,
                    listing_url=f"https://catalog.example/{provider}/checker",
                    tagline=product.tagline,
                )
            )
            asset = PartnerAsset(
                account_id=account.id,
                name=product.name,
                product_id=product.id,
                kind="product",
                status="active",
            )
            session.add(asset)
            session.flush()
            if index == 0:
                partner_analytics.seed(
                    asset_id=asset.id, day=now.date(), impressions=100, clicks=10
                )
            elif index == 1:
                partner_analytics.seed(
                    asset_id=asset.id, day=now.date(), impressions=900, clicks=9, baseline=True
                )
            else:
                partner_analytics.seed(asset_id=asset.id, day=now.date(), impressions=1, clicks=0)
        return [account.id for account in accounts], private.id, paused.id, product.id


def test_dashboard_sums_all_accounts_before_ctr_and_keeps_account_product_rows(
    portal_client, database, partner_analytics
):
    client, _ = portal_client
    accounts, private, paused, product = seed_workspace(database, partner_analytics)
    response = client.get("/v1/partner/dashboard")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "account" not in data
    assert data["account_total"] == data["asset_total"] == 2
    assert data["totals"] == {"impressions": 1000, "clicks": 19, "ctr": 1.9, "measured_days": 1}
    assert data["trend"] == [
        {"day": utcnow().date().isoformat(), "impressions": 1000, "clicks": 19}
    ]
    assert {row["account_id"] for row in data["assets"]} == {str(value) for value in accounts}
    assert {row["product_id"] for row in data["assets"]} == {str(product)}
    assert {row["account_name"] for row in data["assets"]} == {"Account 000", "Account 001"}
    assert data["format_totals"][0]["ctr"] == 1.9
    filtered = client.get("/v1/partner/dashboard", params={"account_id": str(accounts[1])}).json()
    assert filtered["account_total"] == 1 and filtered["totals"]["ctr"] == 1.0
    for value in (private, paused, uuid.uuid4()):
        assert (
            client.get("/v1/partner/dashboard", params={"account_id": str(value)}).status_code
            == 404
        )


def test_more_than_100_memberships_are_scoped_before_global_ranking(
    portal_client, database, partner_analytics
):
    client, _ = portal_client
    accounts, *_ = seed_workspace(database, partner_analytics, count=105)
    response = client.get("/v1/partner/dashboard?sort=impressions&order=asc&limit=1&offset=102")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["account_total"] == data["asset_total"] == 105
    assert data["totals"]["impressions"] == 1103
    assert data["assets"][0]["account_id"] == str(accounts[104])
    last = client.get("/v1/partner/inventory?limit=5&offset=100").json()
    assert last["total"] == 105 and len(last["items"]) == 5
    assert {row["account_id"] for row in last["items"]} == {str(value) for value in accounts[100:]}
    connections = client.get("/v1/partner/connections?limit=100&offset=100").json()
    assert connections["total"] == 105 and len(connections["items"]) == 5
    assert {row["account_id"] for row in connections["items"]} == {
        str(value) for value in accounts[100:]
    }


def test_inventory_is_per_account_policy_and_supports_exact_product_lookup(
    portal_client, database, partner_analytics
):
    client, _ = portal_client
    accounts, private, paused, product = seed_workspace(database, partner_analytics)
    with database.begin() as session:
        session.add(
            PartnerProductPromotion(account_id=accounts[1], product_id=product, excluded=True)
        )
    data = client.get("/v1/partner/inventory", params={"product_id": str(product)}).json()
    assert data["total"] == 2 and data["limit"] == 100 and data["offset"] == 0
    rows = {row["account_id"]: row for row in data["items"]}
    assert rows[str(accounts[0])]["commercial_approved"] is True
    assert (
        rows[str(accounts[1])]["excluded"] is True
        and not rows[str(accounts[1])]["commercial_approved"]
    )
    assert (
        client.get("/v1/partner/inventory", params={"product_id": str(uuid.uuid4())}).json()[
            "total"
        ]
        == 0
    )
    assert client.get("/v1/partner/inventory?q=missing").json()["total"] == 0
    assert client.get("/v1/partner/inventory?q=%25_").json()["total"] == 0
    scoped = client.get(
        "/v1/partner/inventory", params={"account_id": str(accounts[1]), "product_id": str(product)}
    ).json()
    assert len(scoped["items"]) == 1 and scoped["items"][0]["excluded"]
    for value in (private, paused):
        assert (
            client.get("/v1/partner/inventory", params={"account_id": str(value)}).status_code
            == 404
        )


def test_global_connection_records_and_jobs_never_bypass_membership_or_archive(
    portal_client, database, partner_analytics
):
    client, identity = portal_client
    accounts, private, paused, _ = seed_workspace(database, partner_analytics)
    detail = client.get("/v1/partner/connections/catalog-001")
    assert detail.status_code == 200
    assert (
        detail.json()["account_id"] == str(accounts[1])
        and detail.json()["account_name"] == "Account 001"
    )
    with database.begin() as session:
        session.add(
            PartnerPipelineJob(
                provider="catalog-001",
                operation="sync",
                payload={"private": "secret-job-payload"},
                error="sensitive-upstream-error",
            )
        )
    jobs = client.get("/v1/partner/connections/catalog-001/jobs")
    assert jobs.status_code == 200 and jobs.json()["total"] == 1
    assert "secret-job-payload" not in jobs.text and "sensitive-upstream-error" not in jobs.text
    for provider in ("catalog-002", "catalog-003", "missing"):
        assert client.get(f"/v1/partner/connections/{provider}").status_code == 404
        assert client.get(f"/v1/partner/connections/{provider}/jobs").status_code == 404
    with database.begin() as session:
        session.get(PartnerConnection, "catalog-000").archived_at = utcnow()
        session.delete(
            session.get(PartnerMembership, (accounts[1], identity.issuer, identity.subject))
        )
    assert client.get("/v1/partner/connections").json()["total"] == 0
    for provider in ("catalog-000", "catalog-001"):
        assert client.get(f"/v1/partner/connections/{provider}").status_code == 404


def test_global_connection_create_requires_explicit_current_account_association(
    portal_client, database, partner_analytics
):
    client, _ = portal_client
    accounts, private, paused, _ = seed_workspace(database, partner_analytics)
    body = {"name": "New API", "connector": {"base_url": "https://new.example"}, "enabled": False}
    assert client.post("/v1/partner/connections", json=body).status_code == 422
    for value in (private, paused, uuid.uuid4()):
        assert (
            client.post(
                "/v1/partner/connections", json={**body, "account_id": str(value)}
            ).status_code
            == 404
        )
    response = client.post("/v1/partner/connections", json={**body, "account_id": str(accounts[1])})
    assert response.status_code == 201, response.text
    assert (
        response.json()["account_id"] == str(accounts[1])
        and response.json()["account_name"] == "Account 001"
    )


def test_global_search_filter_sort_and_pagination_apply_to_complete_scope(
    portal_client, database, partner_analytics
):
    client, _ = portal_client
    accounts, private, paused, _ = seed_workspace(database, partner_analytics)
    assert client.get("/v1/partner/accounts?q=Account%20001").json()["items"][0]["id"] == str(
        accounts[1]
    )
    assert client.get("/v1/partner/connections?q=Account%20001").json()["total"] == 1
    reverse = client.get("/v1/partner/connections?sort=-account&limit=1").json()
    assert reverse["items"][0]["account_id"] == str(accounts[1])
    assert (
        client.get("/v1/partner/connections", params={"account_id": str(private)}).status_code
        == 404
    )
    assert (
        client.get("/v1/partner/connections", params={"account_id": str(accounts[1])}).json()[
            "total"
        ]
        == 1
    )
    assert client.get("/v1/partner/connections?sort=unknown").status_code == 422
    assert client.get("/v1/partner/dashboard?sort=account&order=desc&limit=1").json()["assets"][0][
        "account_id"
    ] == str(accounts[1])


def test_revoked_or_wrong_issuer_has_no_global_data(portal_client, database, partner_analytics):
    client, identity = portal_client
    seed_workspace(database, partner_analytics)
    identity.issuer = "https://different-issuer.example"
    assert client.get("/v1/partner/dashboard").json()["account_total"] == 0
    assert client.get("/v1/partner/inventory").json()["total"] == 0
    assert client.get("/v1/partner/connections").json()["total"] == 0
    assert client.get("/v1/partner/accounts").json()["total"] == 0


def test_global_query_counts_remain_constant_for_large_membership_sets(
    portal_client, database, partner_analytics
):
    from tests.api.test_api_query_budgets import profile_request

    client, _ = portal_client
    seed_workspace(database, partner_analytics, count=105)
    for path, budget in (
        ("/v1/partner/dashboard?limit=100", 6),
        ("/v1/partner/inventory?limit=100", 2),
        ("/v1/partner/connections?limit=100", 3),
    ):
        _, data = profile_request(client, path, budget)
        assert data.get("asset_total", data.get("total")) == 105
