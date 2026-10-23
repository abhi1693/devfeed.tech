"""Ineligible inventory stays cheap and leaves attribution editable."""

import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from devfeed_core.models import (
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerProductTopic,
    Topic,
    UserAccount,
    UserTopic,
)
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import DBAPIError

from tests.partners.placements.test_placement_rotation import KEY, mark_served, seed_rotation

pytestmark = pytest.mark.integration


@contextmanager
def counted_statements(session):
    """Observe only SQL executed by this reader, excluding setup and lock probes."""
    connection = session.connection()
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", record)


def add_reader(session, *, level="entry_level"):
    reader = UserAccount(
        issuer="https://identity.example",
        subject="placement-query-budget-reader",
        organization_id="org",
        profile={"experience_level": level},
    )
    session.add(reader)
    session.flush()
    return reader.id


def placement_query(catalog, surface, *, grounded=False):
    if surface == "article":
        return PlacementQuery(surface=surface, article_id=catalog["article"])
    if surface == "topic":
        return PlacementQuery(surface=surface, topic_id=catalog["topic"])
    return PlacementQuery(
        surface=surface,
        allow_sponsored_fallback=True,
        article_ids=[catalog["article"]] if grounded else [],
    )


def attribution_rows(catalog, *, include_promotion=False):
    rows = [
        (PartnerAccount, PartnerAccount.id == catalog["account"]),
        (PartnerAsset, PartnerAsset.account_id == catalog["account"]),
        (PartnerProduct, PartnerProduct.id.in_(catalog["products"])),
        (PartnerCampaign, PartnerCampaign.account_id == catalog["account"]),
        (PartnerConnection, PartnerConnection.account_id == catalog["account"]),
        (PartnerListing, PartnerListing.product_id.in_(catalog["products"])),
    ]
    if include_promotion:
        rows.append(
            (PartnerProductPromotion, PartnerProductPromotion.account_id == catalog["account"])
        )
    return rows


def assert_attribution_editable(database, catalog, *, include_promotion=False):
    # Keep the reader's transaction open while a genuine second transaction
    # obtains exclusive locks. NOWAIT makes a retained reader lock fail this test.
    with database.begin() as editor:
        for model, condition in attribution_rows(catalog, include_promotion=include_promotion):
            assert editor.scalars(select(model).where(condition).with_for_update(nowait=True)).all()


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
@pytest.mark.parametrize("count", [105, 205])
@pytest.mark.parametrize("signed_in", [False, True], ids=["anonymous", "mismatched-reader"])
def test_audience_rejections_have_bounded_queries_and_no_attribution_locks(
    database, surface, count, signed_in
):
    catalog = seed_rotation(database, count=count)
    with database.begin() as setup:
        account = setup.get(PartnerAccount, catalog["account"])
        account.tier = "platinum"
        account.audience = {"experience_levels": ["senior"]}
        user_id = add_reader(setup) if signed_in else None

    with database.begin() as reader:
        with counted_statements(reader) as statements:
            rows = deliver_placements(
                reader,
                placement_query(catalog, surface),
                KEY,
                now=catalog["now"],
                user_id=user_id,
            )
        assert rows == []
        assert len(statements) <= 15, f"Executed {len(statements)} SQL statements"
        assert_attribution_editable(database, catalog)
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0


def seed_lock_policy(database, *, count=1):
    catalog = seed_rotation(database, count=count)
    with database.begin() as setup:
        setup.get(PartnerAccount, catalog["account"]).tier = "platinum"
        setup.add(
            PartnerProductPromotion(
                account_id=catalog["account"],
                product_id=catalog["products"][0],
                excluded=False,
            )
        )
    return catalog


def test_audience_edit_after_preflight_is_rechecked_and_releases_late_rejection_locks(database):
    catalog = seed_lock_policy(database)
    edited = False

    def restrict_audience_before_account_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal edited
        if edited or "FROM partner_accounts" not in statement or "FOR UPDATE" not in statement:
            return
        edited = True
        # The editor commits after the reader's candidate snapshot but before
        # attribution locking. This exercises a real stale policy race.
        with database.begin() as editor:
            editor.execute(
                update(PartnerAccount)
                .where(PartnerAccount.id == catalog["account"])
                .values(audience={"experience_levels": ["senior"]})
            )

    with database.begin() as reader:
        connection = reader.connection()
        event.listen(connection, "before_cursor_execute", restrict_audience_before_account_lock)
        try:
            rows = deliver_placements(
                reader, placement_query(catalog, "article"), KEY, now=catalog["now"]
            )
        finally:
            event.remove(connection, "before_cursor_execute", restrict_audience_before_account_lock)
        assert edited
        assert rows == []
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0
        assert_attribution_editable(database, catalog, include_promotion=True)


def test_accepted_placement_keeps_attribution_locked_until_reader_transaction_finishes(database):
    catalog = seed_lock_policy(database)
    with database.begin() as reader:
        rows = deliver_placements(
            reader, placement_query(catalog, "article"), KEY, now=catalog["now"]
        )
        assert [row["product_id"] for row in rows] == catalog["products"]
        for model, condition in attribution_rows(catalog, include_promotion=True):
            with pytest.raises(DBAPIError) as error, database.begin() as editor:
                editor.scalars(select(model).where(condition).with_for_update(nowait=True)).all()
            assert error.value.orig.sqlstate == "55P03"
    assert_attribution_editable(database, catalog, include_promotion=True)


def test_busy_asset_rejection_releases_locks_acquired_before_the_busy_row(database):
    catalog = seed_lock_policy(database)
    with database.begin() as asset_editor:
        assert asset_editor.scalar(
            select(PartnerAsset)
            .where(PartnerAsset.account_id == catalog["account"])
            .with_for_update()
        )
        with database.begin() as reader:
            assert (
                deliver_placements(
                    reader, placement_query(catalog, "article"), KEY, now=catalog["now"]
                )
                == []
            )
            with database.begin() as another_editor:
                for model, condition in attribution_rows(catalog, include_promotion=True):
                    if model is PartnerAsset:
                        continue
                    assert another_editor.scalars(
                        select(model).where(condition).with_for_update(nowait=True)
                    ).all()


@pytest.mark.parametrize(
    "freshness",
    ["current", "aged", "revision_mismatch", "product_time_mismatch", "topic_time_mismatch"],
)
def test_topic_filtered_agreement_only_shadows_current_prepared_product_topics(database, freshness):
    catalog = seed_rotation(database, count=2)
    first = catalog["products"][0]
    with database.begin() as setup:
        setup.get(PartnerAccount, catalog["account"]).tier = "platinum"
        setup.add(
            PartnerCampaign(
                account_id=catalog["account"],
                name="Topic-filtered agreement reserved for senior readers",
                status="active",
                targeting="filters",
                technologies=["Python"],
                topic_ids=[str(catalog["topic"])],
                priority=90,
                audience={"experience_levels": ["senior"]},
                starts_at=catalog["now"] - timedelta(days=1),
                ends_at=catalog["now"] + timedelta(days=1),
            )
        )
        setup.flush()
        ensure_automatic_assets(setup, catalog["account"], catalog["now"])
        match = setup.get(PartnerProductTopic, (first, catalog["topic"]))
        if freshness == "aged":
            match.updated_at = catalog["now"] - timedelta(hours=1)
        elif freshness == "revision_mismatch":
            match.product_revision += 1
        elif freshness == "product_time_mismatch":
            match.product_updated_at -= timedelta(seconds=1)
        elif freshness == "topic_time_mismatch":
            match.topic_updated_at -= timedelta(seconds=1)

    with database.begin() as reader:
        rows = deliver_placements(
            reader, placement_query(catalog, "article"), KEY, now=catalog["now"]
        )
        # An anonymous reader cannot bypass a current topic-targeted agreement's
        # audience. A stale prepared edge cannot reserve the standing inventory.
        assert [row["product_id"] for row in rows] == (
            [] if freshness in {"current", "aged"} else [first]
        )
        if freshness in {"current", "aged"}:
            assert_attribution_editable(database, catalog)


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
def test_later_eligible_product_survives_audience_rejected_batch_with_bounded_queries(
    database, surface
):
    catalog = seed_rotation(database, count=106)
    rejected, eligible = catalog["products"][:-1], catalog["products"][-1]
    with database.begin() as setup:
        setup.get(PartnerAccount, catalog["account"]).tier = "platinum"
        setup.add(
            PartnerCampaign(
                account_id=catalog["account"],
                name="Audience-restricted agreement ahead of eligible inventory",
                status="active",
                targeting="selected",
                product_ids=[str(product_id) for product_id in rejected],
                priority=90,
                audience={"experience_levels": ["senior"]},
                starts_at=catalog["now"] - timedelta(days=1),
                ends_at=catalog["now"] + timedelta(days=1),
            )
        )
        if surface == "feed":
            setup.add(
                ArticleTopic(
                    article_id=catalog["article"],
                    topic_id=catalog["topic"],
                    role="primary",
                    relevance=1,
                    evidence="Python testing tutorial",
                )
            )
        setup.flush()
        ensure_automatic_assets(setup, catalog["account"], catalog["now"])

    with database.begin() as reader:
        with counted_statements(reader) as statements:
            rows = deliver_placements(
                reader,
                placement_query(catalog, surface, grounded=True),
                KEY,
                now=catalog["now"],
            )
        assert [row["product_id"] for row in rows] == [eligible]
        assert len(statements) <= 35, f"Executed {len(statements)} SQL statements"
        # The selected product legitimately holds the shared account lock. Its
        # rejected neighbors' assets, products and listings must remain editable.
        with database.begin() as editor:
            for model in (PartnerAsset, PartnerProduct, PartnerListing):
                product_id = model.id if model is PartnerProduct else model.product_id
                assert editor.scalars(
                    select(model.id).where(product_id.in_(rejected)).with_for_update(nowait=True)
                ).all()
        assert (
            reader.scalar(
                select(func.count())
                .select_from(PartnerPlacementDelivery)
                .join(PartnerAsset)
                .where(PartnerAsset.product_id.in_(rejected))
            )
            == 0
        )


def test_distinct_retired_audience_topics_are_rejected_in_batches_before_attribution_locks(
    database,
):
    catalog = seed_rotation(database, count=105)
    with database.begin() as setup:
        setup.get(PartnerAccount, catalog["account"]).tier = "platinum"
        user_id = add_reader(setup, level="senior")
        setup.add(UserTopic(user_id=user_id, topic_id=catalog["topic"]))
        for index, product_id in enumerate(catalog["products"]):
            retired_id = uuid.uuid4()
            setup.add(
                Topic(
                    id=retired_id,
                    name=f"Retired language {index}",
                    slug=f"retired-language-{index}",
                    kind="language",
                    status="rejected",
                )
            )
            setup.add(
                PartnerCampaign(
                    account_id=catalog["account"],
                    name=f"Retained retired audience {index}",
                    status="active",
                    targeting="selected",
                    product_ids=[str(product_id)],
                    priority=90,
                    audience={
                        "experience_levels": ["senior"],
                        "language_topic_ids": [str(catalog["topic"]), str(retired_id)],
                    },
                    starts_at=catalog["now"] - timedelta(days=1),
                    ends_at=catalog["now"] + timedelta(days=1),
                )
            )
        setup.flush()
        ensure_automatic_assets(setup, catalog["account"], catalog["now"])

    with database.begin() as reader:
        with counted_statements(reader) as statements:
            rows = deliver_placements(
                reader,
                placement_query(catalog, "article"),
                KEY,
                now=catalog["now"],
                user_id=user_id,
            )
        assert rows == []
        assert len(statements) <= 15, f"Executed {len(statements)} SQL statements"
        assert_attribution_editable(database, catalog)


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
@pytest.mark.parametrize("count", [105, 205])
@pytest.mark.parametrize("rejection", ["exhausted_campaign", "missing_listing"])
def test_capacity_and_destination_rejections_are_batched_before_attribution_locks(
    database, surface, count, rejection
):
    catalog = seed_rotation(database, count=count)
    with database.begin() as setup:
        if rejection == "exhausted_campaign":
            campaign = setup.scalar(
                select(PartnerCampaign).where(PartnerCampaign.account_id == catalog["account"])
            )
            campaign.daily_impression_limit = 1
        else:
            setup.get(PartnerAccount, catalog["account"]).outbound_destination = "listing_url"
            setup.execute(
                update(PartnerListing)
                .where(PartnerListing.product_id.in_(catalog["products"]))
                .values(listing_url="")
            )
    if rejection == "exhausted_campaign":
        mark_served(database, catalog, catalog["products"][:1])

    with database.begin() as reader:
        with counted_statements(reader) as statements:
            rows = deliver_placements(
                reader, placement_query(catalog, surface), KEY, now=catalog["now"]
            )
        assert rows == []
        budget = 15 if count == 105 else 20
        assert len(statements) <= budget, f"Executed {len(statements)} SQL statements"
        # An empty preflight result never enters a candidate's locking savepoint.
        assert not any(statement.startswith("SAVEPOINT ") for statement in statements)
        assert_attribution_editable(database, catalog)
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == (
            1 if rejection == "exhausted_campaign" else 0
        )


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
@pytest.mark.parametrize("rejection", ["exhausted_campaign", "missing_listing"])
def test_later_eligible_product_survives_capacity_and_destination_rejected_batches(
    database, surface, rejection
):
    catalog = seed_rotation(database, count=106)
    rejected, eligible = catalog["products"][:-1], catalog["products"][-1]
    with database.begin() as setup:
        if rejection == "exhausted_campaign":
            managed = setup.scalar(
                select(PartnerCampaign).where(PartnerCampaign.account_id == catalog["account"])
            )
            # The separate managed campaign has room for one more reservation.
            # A cached rejection for the explicit agreement must not hide it.
            managed.daily_impression_limit = 2
            setup.add(
                PartnerCampaign(
                    account_id=catalog["account"],
                    name="Exhausted agreement ahead of eligible inventory",
                    status="active",
                    targeting="selected",
                    product_ids=[str(product_id) for product_id in rejected],
                    daily_impression_limit=1,
                    priority=90,
                    starts_at=catalog["now"] - timedelta(days=1),
                    ends_at=catalog["now"] + timedelta(days=1),
                )
            )
            setup.flush()
            ensure_automatic_assets(setup, catalog["account"], catalog["now"])
        else:
            setup.get(PartnerAccount, catalog["account"]).outbound_destination = "listing_url"
            setup.execute(
                update(PartnerListing)
                .where(PartnerListing.product_id.in_(rejected))
                .values(listing_url="")
            )
    if rejection == "exhausted_campaign":
        mark_served(database, catalog, rejected[:1])

    with database.begin() as reader:
        with counted_statements(reader) as statements:
            rows = deliver_placements(
                reader, placement_query(catalog, surface), KEY, now=catalog["now"]
            )
        assert [row["product_id"] for row in rows] == [eligible]
        assert len(statements) <= 40, f"Executed {len(statements)} SQL statements"
        # A successful neighbor keeps its own attribution locked, while rejected
        # products and their assets/listings remain editable in this transaction.
        with database.begin() as editor:
            for model in (PartnerAsset, PartnerProduct, PartnerListing):
                product_id = model.id if model is PartnerProduct else model.product_id
                assert editor.scalars(
                    select(model.id).where(product_id.in_(rejected)).with_for_update(nowait=True)
                ).all()


@pytest.mark.parametrize(
    "change,count",
    [("consume_capacity", 1), ("remove_listing_destination", 1), ("consume_capacity", 105)],
)
def test_capacity_and_destination_edits_after_preflight_are_rechecked_under_lock(
    database, change, count
):
    catalog = seed_lock_policy(database, count=count)
    with database.begin() as setup:
        if change == "consume_capacity":
            campaign = setup.scalar(
                select(PartnerCampaign).where(PartnerCampaign.account_id == catalog["account"])
            )
            campaign.daily_impression_limit = 1
        else:
            setup.get(PartnerAccount, catalog["account"]).outbound_destination = "listing_url"
    edited = False

    def invalidate_before_account_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal edited
        if edited or "FROM partner_accounts" not in statement or "FOR UPDATE" not in statement:
            return
        edited = True
        if change == "consume_capacity":
            mark_served(database, catalog, catalog["products"][:1])
        else:
            with database.begin() as editor:
                editor.execute(
                    update(PartnerListing)
                    .where(PartnerListing.product_id.in_(catalog["products"]))
                    .values(listing_url="")
                )

    with database.begin() as reader:
        connection = reader.connection()
        event.listen(connection, "before_cursor_execute", invalidate_before_account_lock)
        try:
            with counted_statements(reader) as statements:
                rows = deliver_placements(
                    reader,
                    placement_query(catalog, "feed" if count > 1 else "article"),
                    KEY,
                    now=catalog["now"],
                )
        finally:
            event.remove(connection, "before_cursor_execute", invalidate_before_account_lock)
        assert edited, "Candidate did not reach the post-preflight attribution lock"
        assert rows == []
        if count > 1:
            # The first final rejection exhausts the whole campaign. Candidates
            # already yielded by the batch must not each retry its locked checks.
            assert sum(statement.startswith("SAVEPOINT ") for statement in statements) == 1
            assert len(statements) < 25, f"Executed {len(statements)} SQL statements"
        assert_attribution_editable(database, catalog, include_promotion=True)
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == (
            1 if change == "consume_capacity" else 0
        )
