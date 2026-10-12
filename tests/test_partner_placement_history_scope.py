"""Rotation reads candidate history without probing unrelated catalog receipts."""

import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerArticleMatch,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
)
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import delete, event, insert, select, text
from test_partner_placement_rotation import KEY, issue, seed_rotation

pytestmark = pytest.mark.integration


def query_for(catalog, surface):
    return PlacementQuery(
        surface=surface,
        **(
            {"allow_sponsored_fallback": True}
            if surface == "feed"
            else {"topic_id": catalog["topic"]}
            if surface == "topic"
            else {"article_id": catalog["article"]}
        ),
    )


def use_organic_assets(database, catalog):
    with database.begin() as session:
        session.get(PartnerAccount, catalog["account"]).promotion_kind = "product"
        for asset in session.scalars(
            select(PartnerAsset).where(PartnerAsset.account_id == catalog["account"])
        ):
            asset.status = "paused"
        ensure_automatic_assets(session, catalog["account"], catalog["now"])


def seed_unrelated_history(database, catalog, *, surface, promotion_kind, count=2000):
    """Contextual noise is live inventory; fallback noise has a paused owner."""
    now = catalog["now"]
    with database.begin() as session:
        account = PartnerAccount(
            name="Unrelated catalog",
            tier="silver",
            status="paused" if surface == "feed" else "active",
            promotion_kind=promotion_kind,
        )
        session.add(account)
        session.flush()
        session.add(PartnerConnection(provider="unrelated", account_id=account.id, enabled=True))
        campaign_id = None
        if promotion_kind == "ad":
            campaign = PartnerCampaign(
                account_id=account.id,
                name="Unrelated sponsorship",
                status="active",
                managed=True,
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=1),
            )
            session.add(campaign)
            session.flush()
            campaign_id = campaign.id
        products = [uuid.UUID(int=10_000 + index) for index in range(count)]
        assets = [uuid.UUID(int=50_000 + index) for index in range(count)]
        session.execute(
            insert(PartnerProduct),
            [
                {
                    "id": product_id,
                    "name": f"Unrelated product {index}",
                    "product_url": f"https://unrelated{index}.example/",
                    "description": "No prepared match for the requested context",
                    "status": "approved",
                    "verified_at": now,
                }
                for index, product_id in enumerate(products)
            ],
        )
        session.execute(
            insert(PartnerListing),
            [
                {
                    "provider": "unrelated",
                    "external_id": str(product_id),
                    "product_id": product_id,
                    "name": f"Unrelated product {index}",
                    "product_url": f"https://unrelated{index}.example/",
                    "listing_url": f"https://catalog.example/unrelated/{index}",
                    "description": "No prepared context match",
                }
                for index, product_id in enumerate(products)
            ],
        )
        session.execute(
            insert(PartnerAsset),
            [
                {
                    "id": asset_id,
                    "account_id": account.id,
                    "product_id": product_id,
                    "campaign_id": campaign_id,
                    "automatic": True,
                    "name": f"Unrelated placement {index}",
                    "kind": promotion_kind,
                    "status": "active",
                }
                for index, (product_id, asset_id) in enumerate(zip(products, assets, strict=True))
            ],
        )
        session.execute(
            insert(PartnerPlacementDelivery),
            [
                {
                    "delivery_id": uuid.UUID(int=100_000 + index * 8 + receipt),
                    "asset_id": asset_id,
                    "issued_at": now - timedelta(minutes=receipt + 1),
                    "surface": "feed",
                }
                for index, asset_id in enumerate(assets)
                for receipt in range(8)
            ],
        )
        session.flush()
        # Statistics must represent the unrelated inventory, rather than the
        # otherwise freshly truncated tables' initial near-empty estimates.
        for table in ("partner_assets", "partner_placement_deliveries"):
            session.execute(text(f"ANALYZE {table}"))


@contextmanager
def captured_ranking_queries(session):
    connection = session.connection()
    captured = []

    def capture(_connection, _cursor, sql, parameters, _context, _executemany):
        if "partner_placement_deliveries" in sql and "max(" in sql.lower():
            captured.append((sql, parameters))

    event.listen(connection, "before_cursor_execute", capture)
    try:
        yield captured
    finally:
        event.remove(connection, "before_cursor_execute", capture)


def history_work(plan):
    """Count receipt probes and visited rows, including scans that discard rows."""
    children = [history_work(child) for child in plan.get("Plans", [])]
    loops = sum(child[0] for child in children)
    visited = sum(child[1] for child in children)
    if plan.get("Relation Name") == "partner_placement_deliveries":
        loops += plan.get("Actual Loops", 0)
        visited += plan.get("Actual Loops", 0) * sum(
            plan.get(field, 0)
            for field in ("Actual Rows", "Rows Removed by Filter", "Rows Removed by Index Recheck")
        )
    return loops, visited


def explain_history_work(session, captured):
    assert captured, "Expected to observe the application's placement ranking SQL"
    work = []
    for sql, parameters in captured:
        result = session.connection().exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters
        )
        work.append(history_work(result.scalar_one()[0]["Plan"]))
    return tuple(sum(values) for values in zip(*work, strict=True))


@pytest.mark.parametrize(
    "surface,promotion_kind",
    [("article", "ad"), ("topic", "ad"), ("article", "product"), ("feed", "ad")],
)
def test_ranking_only_probes_history_for_products_in_the_current_phase(
    database, surface, promotion_kind
):
    catalog = seed_rotation(database)
    if promotion_kind == "product":
        use_organic_assets(database, catalog)
    seed_unrelated_history(database, catalog, surface=surface, promotion_kind=promotion_kind)
    with database.begin() as session:
        assets = session.scalars(
            select(PartnerAsset)
            .where(PartnerAsset.account_id == catalog["account"], PartnerAsset.status == "active")
            .order_by(PartnerAsset.product_id)
        ).all()
        session.add_all(
            PartnerPlacementDelivery(
                delivery_id=uuid.uuid4(),
                asset_id=asset.id,
                issued_at=catalog["now"] - timedelta(minutes=len(assets) - index),
                surface="feed",
            )
            for index, asset in enumerate(assets)
        )

    with database.begin() as session:
        with captured_ranking_queries(session) as captured:
            rows = deliver_placements(session, query_for(catalog, surface), KEY, now=catalog["now"])
        assert [row["product_id"] for row in rows] == catalog["products"][:1]
        probes, visited = explain_history_work(session, captured)
        # Three candidates have one active asset and at most one paused sibling.
        # Allow planner alternatives without evaluating 2,000 unrelated assets.
        assert probes <= 12, f"Ranking made {probes} receipt probes"
        assert visited <= 12, f"Ranking visited {visited} receipt rows"


def test_empty_context_does_not_read_unrelated_receipt_history(database):
    catalog = seed_rotation(database)
    seed_unrelated_history(database, catalog, surface="article", promotion_kind="ad")
    with database.begin() as session:
        session.execute(delete(PartnerArticleMatch))

    with database.begin() as session:
        with captured_ranking_queries(session) as captured:
            rows = deliver_placements(
                session, query_for(catalog, "article"), KEY, now=catalog["now"]
            )
        assert rows == []
        assert explain_history_work(session, captured) == (0, 0)


def test_rotation_uses_latest_receipt_across_manual_paused_and_other_account_assets(database):
    catalog = seed_rotation(database)
    with database.begin() as session:
        previous_owner = PartnerAccount(name="Previous owner", tier="gold", status="paused")
        session.add(previous_owner)
        session.flush()
        siblings = [
            PartnerAsset(
                account_id=previous_owner.id,
                product_id=catalog["products"][0],
                name="Paused manual sponsorship",
                kind="ad",
                ad_format=ad_format,
                status="paused",
                automatic=False,
            )
            for ad_format in ("homepage_sponsorship", "engagement")
        ]
        session.add_all(siblings)
        session.flush()
        assets = {
            asset.product_id: asset
            for asset in session.scalars(
                select(PartnerAsset).where(PartnerAsset.account_id == catalog["account"])
            )
        }
        for asset, minutes in (
            (assets[catalog["products"][0]], 60),
            (siblings[0], 120),
            (siblings[1], 5),
            (assets[catalog["products"][1]], 10),
        ):
            session.add(
                PartnerPlacementDelivery(
                    delivery_id=uuid.uuid4(),
                    asset_id=asset.id,
                    issued_at=catalog["now"] - timedelta(minutes=minutes),
                    surface="feed",
                )
            )

    observed = []
    for index in range(3):
        rows = issue(
            database, catalog, surface="article", now=catalog["now"] + timedelta(seconds=index)
        )
        assert len(rows) == 1
        observed.append(rows[0]["product_id"])
        assert rows[0]["asset_id"] == assets[rows[0]["product_id"]].id
    # No history wins first; the previous owner's recent manual placement keeps
    # product zero behind product one's older last appearance.
    assert observed == [catalog["products"][index] for index in (2, 1, 0)]
