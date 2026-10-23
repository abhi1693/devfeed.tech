"""Final topic validation serializes delivery with prepared-match invalidation."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductTopic,
    Topic,
    TopicRelation,
)
from devfeed_core.partner_matching import TAXONOMY_LOCK_KEY
from devfeed_core.partner_placements import deliver_placements
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import delete, event, func, select, text

from tests.partners.placements.surfaces.test_native_formats import native_account
from tests.partners.placements.test_placement_query_budgets import (
    assert_attribution_editable,
    counted_statements,
)
from tests.partners.placements.test_placement_rotation import seed_rotation
from tests.support.partner_placements import KEY, finish_matching
from tests.support.partner_placements import catalog as catalog

pytestmark = pytest.mark.integration


@pytest.fixture
def related_catalog(database, catalog):
    with database.begin() as session:
        related = Topic(name="Containers", slug="containers", kind="concept", status="active")
        session.add(related)
        session.flush()
        session.add(
            TopicRelation(
                topic_id=catalog["topic"], related_topic_id=related.id, relation="part_of"
            )
        )
        related_id = related.id
    finish_matching(database)
    with database() as session:
        for product in ("paid_product", "free_product"):
            edge = session.get(PartnerProductTopic, (catalog[product], related_id))
            assert edge is not None and edge.score == 65
    return {**catalog, "related_topic": related_id}


def related_request(catalog, sponsored):
    return PlacementQuery(
        surface="topic",
        topic_id=catalog["related_topic"],
        exclude_product_ids=[
            catalog["free_product" if sponsored else "paid_product"],
            catalog["unrelated"],
        ],
    )


def remove_relationship(session, catalog):
    session.execute(
        delete(TopicRelation).where(
            TopicRelation.topic_id == catalog["topic"],
            TopicRelation.related_topic_id == catalog["related_topic"],
            TopicRelation.relation == "part_of",
        )
    )


def assert_attribution_unlocked(database, catalog, sponsored):
    account_id = catalog["paid" if sponsored else "free"]
    product_id = catalog["paid_product" if sponsored else "free_product"]
    with database.begin() as editor:
        assert editor.scalar(
            select(PartnerAccount.id)
            .where(PartnerAccount.id == account_id)
            .with_for_update(nowait=True)
        )
        assert editor.scalars(
            select(PartnerAsset.id)
            .where(PartnerAsset.account_id == account_id, PartnerAsset.product_id == product_id)
            .with_for_update(nowait=True)
        ).all()
        assert editor.scalar(
            select(PartnerProduct.id)
            .where(PartnerProduct.id == product_id)
            .with_for_update(nowait=True)
        )


@pytest.mark.parametrize("sponsored", [True, False], ids=["sponsored", "ordinary"])
def test_relation_edit_waits_until_validated_placement_transaction_commits(
    database, related_catalog, sponsored
):
    started = Event()
    editor_pid = []

    def remove_after_validation():
        with database.begin() as editor:
            editor.execute(text("SET LOCAL lock_timeout = '5s'"))
            editor_pid.append(editor.scalar(select(func.pg_backend_pid())))
            started.set()
            remove_relationship(editor, related_catalog)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.begin() as reader:
            items = deliver_placements(reader, related_request(related_catalog, sponsored), KEY)
            assert len(items) == 1 and items[0]["sponsored"] is sponsored
            future = executor.submit(remove_after_validation)
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            with database() as observer:
                assert observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted)"
                    ),
                    {"pid": editor_pid[0]},
                )
        future.result(timeout=5)

    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 1
        assert (
            session.get(
                PartnerProductTopic,
                (
                    related_catalog["paid_product" if sponsored else "free_product"],
                    related_catalog["related_topic"],
                ),
            )
            is None
        )


@pytest.mark.parametrize("sponsored", [True, False], ids=["sponsored", "ordinary"])
def test_busy_taxonomy_skips_contextual_placement_without_retaining_attribution_locks(
    database, related_catalog, sponsored
):
    with database.begin() as editor:
        editor.execute(select(func.pg_advisory_xact_lock(TAXONOMY_LOCK_KEY)))
        with database.begin() as reader:
            reader.execute(text("SET LOCAL lock_timeout = '100ms'"))
            assert (
                deliver_placements(reader, related_request(related_catalog, sponsored), KEY) == []
            )
            assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0
            assert_attribution_unlocked(database, related_catalog, sponsored)


def test_contextless_fallback_remains_available_during_taxonomy_edit(database, catalog):
    native_account(database, catalog, ["feed"])
    with database.begin() as editor:
        editor.execute(select(func.pg_advisory_xact_lock(TAXONOMY_LOCK_KEY)))
        with database.begin() as reader:
            reader.execute(text("SET LOCAL lock_timeout = '100ms'"))
            items = deliver_placements(
                reader,
                PlacementQuery(
                    surface="feed",
                    allow_sponsored_fallback=True,
                    exclude_product_ids=[catalog["free_product"], catalog["unrelated"]],
                ),
                KEY,
            )
            assert len(items) == 1
            assert items[0]["product_id"] == catalog["paid_product"]
            assert items[0]["sponsored"] is True
            assert items[0]["reason"] == "Sponsored by this partner"


def test_busy_taxonomy_has_bounded_queries_across_large_contextual_catalog(database):
    inventory = seed_rotation(database, count=105)
    with database.begin() as editor:
        editor.execute(select(func.pg_advisory_xact_lock(TAXONOMY_LOCK_KEY)))
        with database.begin() as reader:
            reader.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with counted_statements(reader) as statements:
                items = deliver_placements(
                    reader,
                    PlacementQuery(surface="topic", topic_id=inventory["topic"]),
                    KEY,
                    now=inventory["now"],
                )
            assert items == []
            assert len(statements) <= 15, f"Executed {len(statements)} SQL statements"
            assert sum(statement.startswith("SAVEPOINT ") for statement in statements) == 1
            assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0
            assert_attribution_editable(database, inventory)


def test_missing_final_match_releases_candidate_taxonomy_and_attribution_locks(
    database, related_catalog
):
    invalidated = False
    checked_barrier = False

    def invalidate_before_product_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal invalidated, checked_barrier
        if "pg_try_advisory_xact_lock_shared" in statement:
            checked_barrier = True
        if invalidated or "FROM partner_products" not in statement or "FOR UPDATE" not in statement:
            return
        invalidated = True
        with database.begin() as editor:
            remove_relationship(editor, related_catalog)

    with database.begin() as reader:
        connection = reader.connection()
        event.listen(connection, "before_cursor_execute", invalidate_before_product_lock)
        try:
            assert deliver_placements(reader, related_request(related_catalog, True), KEY) == []
        finally:
            event.remove(connection, "before_cursor_execute", invalidate_before_product_lock)
        assert invalidated and checked_barrier
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0
        with database.begin() as editor:
            assert editor.scalar(select(func.pg_try_advisory_xact_lock(TAXONOMY_LOCK_KEY)))
        assert_attribution_unlocked(database, related_catalog, True)
