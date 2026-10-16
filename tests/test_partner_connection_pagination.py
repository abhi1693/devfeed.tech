"""Connection pages batch catalog statistics without changing ownership policy."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerConnection,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductPromotion,
    utcnow,
)
from sqlalchemy import event
from test_api_query_budgets import profile_request

pytestmark = pytest.mark.integration
PATH = "/v1/admin/partner-tools/connections"


def product(session, key, **changes):
    row = PartnerProduct(
        name=key,
        product_url=f"https://{key}.example/",
        tagline="Developer tooling for API compatibility checks.",
        **changes,
    )
    session.add(row)
    session.flush()
    return row


def listing(session, provider, row, *, suffix="", **changes):
    session.add(
        PartnerListing(
            provider=provider,
            product_id=row.id,
            external_id=row.name + suffix,
            name=row.name,
            product_url=row.product_url,
            listing_url=f"https://platform.example/{provider}/{row.name}{suffix}",
            tagline=row.tagline,
            **changes,
        )
    )


def seed_connections(database, start, stop):
    now = utcnow()
    with database.begin() as session:
        for index in range(start, stop):
            account = PartnerAccount(
                name=f"Account {index:03}",
                tier="silver",
                approval_mode="trusted" if index % 3 == 0 else "qualified",
            )
            session.add(account)
            session.flush()
            provider = f"catalog-{index:03}"
            session.add(
                PartnerConnection(
                    provider=provider,
                    name=f"Catalog {index:03}",
                    enabled=True,
                    account_id=account.id,
                )
            )
            row = product(
                session,
                f"product-{index:03}",
                status="approved" if index % 2 == 0 else "pending",
                verified_at=now if index % 2 == 0 else None,
            )
            listing(session, provider, row)
            session.add(
                PartnerProductPromotion(
                    account_id=account.id,
                    product_id=row.id,
                    commercial_approved_at=now,
                    approval_source="trusted",
                )
            )


@contextmanager
def no_catalog_hydration():
    loaded = []

    def capture(instance, context):
        loaded.append(type(instance).__name__)

    models = (PartnerAccount, PartnerProduct, PartnerProductPromotion)
    for model in models:
        event.listen(model, "load", capture)
    try:
        yield
        assert not loaded, f"Connection summaries hydrated catalog rows: {loaded}"
    finally:
        for model in models:
            event.remove(model, "load", capture)


def test_statistics_query_budget_is_constant_across_large_pages(admin_client, database):
    seed_connections(database, 0, 1)
    with no_catalog_hydration():
        small_profile, small = profile_request(admin_client, PATH + "?limit=100", 3)
    assert small["total"] == 1 and len(small["items"]) == 1
    seed_connections(database, 1, 151)
    with no_catalog_hydration():
        large_profile, first = profile_request(admin_client, PATH + "?limit=100", 3)
        assert large_profile["queries"] == small_profile["queries"]
        assert first["total"] == 151 and first["limit"] == 100 and first["offset"] == 0
        assert [row["provider"] for row in first["items"]] == [
            f"catalog-{index:03}" for index in range(100)
        ]
        for index, row in enumerate(first["items"]):
            assert row["products"] == 1
            assert row["qualified"] == int(index % 2 == 0)
            assert row["commercial_approved"] == int(index % 2 == 0 or index % 3 == 0)
        _, second = profile_request(admin_client, PATH + "?limit=100&offset=100", 3)
        assert second["total"] == 151 and second["offset"] == 100
        assert [row["provider"] for row in second["items"]] == [
            f"catalog-{index:03}" for index in range(100, 151)
        ]
        _, detail = profile_request(admin_client, PATH + "/catalog-150", 2)
        assert detail == second["items"][-1]
        assert admin_client.get(PATH + "/missing").status_code == 404


def test_search_sort_and_stable_ties_apply_before_pagination(admin_client, database):
    names = {
        "omega": "Shared",
        "beta": "Shared",
        "delta": "Shared",
        "alpha": "Alpha launch",
        "epsilon": "Zeta catalog",
        "gamma": "Literal %_ Catalog",
    }
    with database.begin() as session:
        session.add_all(
            PartnerConnection(provider=provider, name=name) for provider, name in names.items()
        )
    default = admin_client.get(PATH).json()
    assert [row["provider"] for row in default["items"]] == sorted(names)
    reverse = admin_client.get(PATH, params={"sort": "-provider", "limit": 2}).json()
    assert reverse["total"] == 6
    assert [row["provider"] for row in reverse["items"]] == ["omega", "gamma"]
    descending = admin_client.get(PATH, params={"sort": "-name"}).json()
    assert [row["provider"] for row in descending["items"]] == [
        "epsilon",
        "beta",
        "delta",
        "omega",
        "gamma",
        "alpha",
    ]
    first = admin_client.get(PATH, params={"q": " shared ", "sort": "name", "limit": 2}).json()
    second = admin_client.get(
        PATH, params={"q": "SHARED", "sort": "name", "limit": 2, "offset": 2}
    ).json()
    assert first["total"] == second["total"] == 3
    assert [row["provider"] for row in first["items"] + second["items"]] == [
        "beta",
        "delta",
        "omega",
    ]
    for query, expected in (("alpha-launch", "alpha"), ("%_", "gamma")):
        page = admin_client.get(PATH, params={"q": query}).json()
        assert page["total"] == 1 and page["items"][0]["provider"] == expected
    assert admin_client.get(PATH, params={"q": "absent", "offset": 99}).json() == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 99,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"sort": "products"},
        {"sort": "--provider"},
        {"q": "x" * 201},
    ],
)
def test_connection_pagination_rejects_invalid_queries(admin_client, params):
    assert admin_client.get(PATH, params=params).status_code == 422


@pytest.fixture
def connection_catalog(database):
    now = utcnow()
    with database.begin() as session:
        account = PartnerAccount(name="Trusted owner", tier="silver", approval_mode="trusted")
        other = PartnerAccount(name="Other owner", tier="silver", approval_mode="trusted")
        session.add_all([account, other])
        session.flush()
        session.add_all(
            [
                PartnerConnection(provider="catalog", account_id=account.id, enabled=True),
                PartnerConnection(provider="sibling", account_id=account.id, enabled=True),
                PartnerConnection(provider="other", account_id=other.id, enabled=True),
            ]
        )
        products = {}
        for key, changes in {
            "fresh": {"status": "approved", "verified_at": now},
            "expired": {"status": "approved", "verified_at": now - timedelta(days=91)},
            "pending-checking": {"assessment": {"state": "checking"}},
            "pending-attention": {},
            "rejected-checking": {"status": "rejected", "assessment": {"state": "checking"}},
            "excluded": {
                "status": "approved",
                "verified_at": now,
                "excluded": True,
                "assessment": {"state": "checking"},
            },
            "trusted": {},
            "promotion-excluded": {"status": "approved", "verified_at": now},
            "inactive": {"status": "approved", "verified_at": now},
            "unresolved": {"status": "approved", "verified_at": now},
            "sibling-owned": {"status": "approved", "verified_at": now},
            "merged": {"status": "approved", "verified_at": now},
            "foreign-trusted": {},
            "trusted-expired": {
                "status": "approved",
                "verified_at": now - timedelta(days=91),
            },
            "withdrawn": {"status": "withdrawn"},
        }.items():
            row = product(session, key, **changes)
            products[key] = row
            listing(
                session,
                "catalog",
                row,
                active=key not in {"inactive", "sibling-owned"},
                identity_status="unresolved" if key == "unresolved" else "resolved",
            )
        products["merged"].merged_into_id = products["fresh"].id
        listing(session, "catalog", products["fresh"], suffix="-duplicate")
        listing(session, "sibling", products["sibling-owned"])
        listing(session, "other", products["foreign-trusted"])
        for key in ("trusted", "trusted-expired", "promotion-excluded"):
            session.add(
                PartnerProductPromotion(
                    account_id=account.id,
                    product_id=products[key].id,
                    commercial_approved_at=now,
                    approval_source="trusted",
                    excluded=key == "promotion-excluded",
                )
            )
        session.add(
            PartnerProductPromotion(
                account_id=other.id,
                product_id=products["foreign-trusted"].id,
                commercial_approved_at=now,
                approval_source="trusted",
            )
        )
        account_id = account.id
    return account_id


def assert_catalog_totals(row, *, commercial):
    assert {
        key: row[key]
        for key in (
            "products",
            "qualified",
            "checking",
            "needs_attention",
            "excluded",
            "commercial_approved",
        )
    } == {
        "products": 15,
        "qualified": 8,
        "checking": 2,
        "needs_attention": 2,
        "excluded": 1,
        "commercial_approved": commercial,
    }


def test_catalog_counts_distinct_products_and_commercial_ownership(
    admin_client, connection_catalog
):
    _, page = profile_request(admin_client, PATH, 3)
    rows = {row["provider"]: row for row in page["items"]}
    assert_catalog_totals(rows["catalog"], commercial=4)
    assert rows["catalog"]["approval_mode"] == "trusted"
    assert rows["sibling"]["products"] == rows["sibling"]["commercial_approved"] == 1
    assert rows["other"]["products"] == rows["other"]["commercial_approved"] == 1
    _, detail = profile_request(admin_client, PATH + "/catalog", 2)
    assert detail == rows["catalog"]


@pytest.mark.parametrize("policy", ["qualified", "paused", "disabled", "unowned"])
def test_commercial_counts_apply_live_account_and_connection_policy(
    admin_client, database, connection_catalog, policy
):
    with database.begin() as session:
        account = session.get(PartnerAccount, connection_catalog)
        connection = session.get(PartnerConnection, "catalog")
        if policy == "qualified":
            account.approval_mode = "qualified"
        elif policy == "paused":
            account.status = "paused"
        elif policy == "disabled":
            connection.enabled = False
        else:
            connection.account_id = None
    response = admin_client.get(PATH + "/catalog")
    assert response.status_code == 200, response.text
    assert_catalog_totals(response.json(), commercial=2 if policy == "qualified" else 0)
    if policy == "unowned":
        assert response.json()["approval_mode"] is None


def test_checking_is_not_restricted_to_pending_and_attention_never_goes_negative(
    admin_client, database
):
    with database.begin() as session:
        session.add(PartnerConnection(provider="catalog"))
        for index in range(3):
            row = product(
                session,
                f"checking-{index}",
                status="pending" if index == 0 else "approved",
                assessment={} if index == 0 else {"state": "checking"},
            )
            listing(session, "catalog", row)
    row = admin_client.get(PATH + "/catalog").json()
    assert row["products"] == 3 and row["qualified"] == 2
    assert row["checking"] == 2 and row["needs_attention"] == 0


@pytest.mark.parametrize(
    "enabled,operation,status,error,expected",
    [
        (True, "sync", "queued", None, "syncing"),
        (True, "sync", "running", "Previous failure", "syncing"),
        (True, "sync", "succeeded", "Previous failure", "error"),
        (True, "assess", "queued", None, "idle"),
        (False, "sync", "queued", "Previous failure", "disconnected"),
    ],
)
def test_connection_state_uses_only_active_sync_jobs(
    admin_client, database, enabled, operation, status, error, expected
):
    with database.begin() as session:
        session.add(PartnerConnection(provider="catalog", enabled=enabled, last_error=error))
        session.flush()
        session.add(PartnerPipelineJob(provider="catalog", operation=operation, status=status))
    row = admin_client.get(PATH + "/catalog").json()
    assert row["state"] == expected and row["error"] == error
    assert (row["next_sync_at"] is not None) is enabled
    assert all(
        row[key] == 0
        for key in (
            "products",
            "qualified",
            "checking",
            "needs_attention",
            "excluded",
            "commercial_approved",
        )
    )
