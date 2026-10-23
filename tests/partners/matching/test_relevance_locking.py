"""Concurrent taxonomy edits cannot lend a placement stale contextual relevance."""

from contextlib import contextmanager

import pytest
from devfeed_core.models import (
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductTopic,
    Topic,
    TopicRelation,
)
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import event, func, select, update

from tests.partners.placements.test_placement_query_budgets import assert_attribution_editable
from tests.partners.placements.test_placement_rotation import KEY, seed_rotation
from tests.support.partner_placements import finish_matching

pytestmark = pytest.mark.integration


def related_topic_catalog(database, *, sponsored=True, count=1):
    catalog = seed_rotation(database, count=count)
    with database.begin() as setup:
        related = Topic(name="Containers", slug="containers", kind="concept", status="active")
        setup.add(related)
        setup.flush()
        setup.add(
            TopicRelation(
                topic_id=catalog["topic"], related_topic_id=related.id, relation="part_of"
            )
        )
        for product_id in catalog["products"]:
            setup.get(PartnerProduct, product_id).evidence = [
                {
                    "quote": "Python testing applications and inspecting services.",
                    "capability": "Testing applications and inspecting services",
                }
            ]
        if not sponsored:
            setup.get(PartnerAccount, catalog["account"]).promotion_kind = "product"
            setup.flush()
            ensure_automatic_assets(setup, catalog["account"], catalog["now"])
        setup.add(
            ArticleTopic(
                article_id=catalog["article"],
                topic_id=related.id,
                role="primary",
                relevance=1,
                evidence="Container tooling for the displayed tutorial.",
            )
        )
        catalog["related"] = related.id
    # Exercise the actual worker and invalidation triggers, rather than planting
    # a synthetic match whose lifecycle differs from serving preparation.
    finish_matching(database)
    with database() as check:
        for product_id in catalog["products"]:
            assert check.get(PartnerProductTopic, (product_id, catalog["related"])).score == 65
    return catalog


def contextual_query(catalog, surface):
    return PlacementQuery(
        surface=surface,
        **(
            {"topic_id": catalog["related"]}
            if surface == "topic"
            else {"article_ids": [catalog["article"]]}
        ),
    )


@contextmanager
def edit_before_attribution_lock(reader, edit):
    """Commit a real second transaction after ranking and before its first lock."""
    edited = []
    connection = reader.connection()

    def intercept(_connection, _cursor, statement, _parameters, _context, _executemany):
        if not edited and "FROM partner_accounts" in statement and "FOR UPDATE" in statement:
            edit()
            edited.append(True)

    event.listen(connection, "before_cursor_execute", intercept)
    try:
        yield edited
    finally:
        event.remove(connection, "before_cursor_execute", intercept)


def remove_relation(database, catalog):
    identity = catalog["topic"], catalog["related"], "part_of"
    with database.begin() as editor:
        editor.delete(editor.get(TopicRelation, identity))
    # The editor has committed before the reader obtains attribution locks. The
    # trigger must have removed the worker-produced edge in that same commit.
    with database() as check:
        assert check.get(TopicRelation, identity) is None
        assert check.get(PartnerProductTopic, (catalog["products"][0], catalog["related"])) is None


@pytest.mark.parametrize("surface", ["topic", "feed"])
@pytest.mark.parametrize("sponsored", [True, False], ids=["sponsored", "organic"])
def test_relation_removed_after_ranking_rejects_stale_context_and_releases_locks(
    database, surface, sponsored
):
    catalog = related_topic_catalog(database, sponsored=sponsored)
    with database.begin() as reader:
        with edit_before_attribution_lock(
            reader, lambda: remove_relation(database, catalog)
        ) as edits:
            rows = deliver_placements(
                reader, contextual_query(catalog, surface), KEY, now=catalog["now"]
            )
        assert edits == [True]
        assert rows == []
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0
        # Failed final checks must roll back candidate savepoints, so an editor
        # does not wait for the otherwise still-open request transaction.
        assert_attribution_editable(database, catalog)


@pytest.mark.parametrize("surface", ["topic", "feed"])
@pytest.mark.parametrize("sponsored", [True, False], ids=["sponsored", "organic"])
def test_current_context_still_delivers_with_its_current_reason(database, surface, sponsored):
    catalog = related_topic_catalog(database, sponsored=sponsored)
    current_reason = "Current verified connection to container tooling."

    def refresh_reason():
        with database.begin() as editor:
            editor.execute(
                update(PartnerProductTopic)
                .where(
                    PartnerProductTopic.product_id == catalog["products"][0],
                    PartnerProductTopic.topic_id == catalog["related"],
                )
                .values(reason=current_reason)
            )

    with database.begin() as reader:
        with edit_before_attribution_lock(reader, refresh_reason) as edits:
            rows = deliver_placements(
                reader, contextual_query(catalog, surface), KEY, now=catalog["now"]
            )
        assert edits == [True]
        assert [row["product_id"] for row in rows] == catalog["products"]
        assert rows[0]["sponsored"] is sponsored
        assert rows[0]["reason"] == current_reason
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 1


@pytest.mark.parametrize("surface", ["topic", "feed"])
def test_late_relevance_rejection_keeps_other_current_candidates_available(database, surface):
    catalog = related_topic_catalog(database, count=2)
    survivor = catalog["products"][1]
    with database.begin() as setup:
        rust = Topic(name="Rust", slug="rust", kind="language", status="active")
        setup.add(rust)
        setup.flush()
        setup.add(
            TopicRelation(topic_id=rust.id, related_topic_id=catalog["related"], relation="part_of")
        )
        product = setup.get(PartnerProduct, survivor)
        product.technologies = ["Rust"]
        product.evidence = [
            {
                "quote": "Rust testing applications and inspecting services.",
                "capability": "Testing applications and inspecting services",
            }
        ]
    finish_matching(database)

    def edit_and_prepare():
        remove_relation(database, catalog)
        # Both products' old edges are invalidated atomically. Only the genuinely
        # supported product regains this context when the targeted worker runs.
        finish_matching(database)
        with database() as check:
            assert (
                check.get(PartnerProductTopic, (catalog["products"][0], catalog["related"])) is None
            )
            assert check.get(PartnerProductTopic, (survivor, catalog["related"])).score == 65

    with database.begin() as reader:
        with edit_before_attribution_lock(reader, edit_and_prepare) as edits:
            rows = deliver_placements(
                reader, contextual_query(catalog, surface), KEY, now=catalog["now"]
            )
        assert edits == [True]
        assert [row["product_id"] for row in rows] == [survivor]
        deliveries = reader.scalars(
            select(PartnerAsset.product_id).join(
                PartnerPlacementDelivery, PartnerPlacementDelivery.asset_id == PartnerAsset.id
            )
        ).all()
        assert deliveries == [survivor]
