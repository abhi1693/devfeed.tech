"""Global header sponsorship is independent paid inventory with live policy fences."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core import partner_placements as placements
from devfeed_core.models import (
    Article,
    PartnerAccount,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductPromotion,
    UserTopic,
    utcnow,
)
from devfeed_core.partner_ad_formats import placement_format_allowed
from devfeed_core.partner_matching import TAXONOMY_LOCK_KEY
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tracking import (
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from devfeed_http.partner_placements import PlacementQuery
from devfeed_http.partner_portal import CampaignInput
from pydantic import ValidationError
from sqlalchemy import func, select, update
from test_partner_feed_placements import feed_catalog as feed_catalog
from test_partner_native_formats import native_account
from test_partner_placement_query_budgets import (
    assert_attribution_editable,
    counted_statements,
)
from test_partner_placement_rotation import seed_rotation
from test_partner_placements_integration import KEY, deliver
from test_partner_placements_integration import catalog as catalog
from test_partner_relevance_locking import (
    edit_before_attribution_lock,
    related_topic_catalog,
    remove_relation,
)


def header_query(**context):
    return PlacementQuery(surface="header", ad_formats=["header_sponsorship"], **context)


@pytest.mark.parametrize(
    "context",
    [{}, {"article_id": uuid.uuid4()}, {"topic_id": uuid.uuid4()}, {"article_ids": [uuid.uuid4()]}],
)
def test_header_has_optional_bounded_context_and_its_own_format(context):
    query = header_query(**context)
    assert placement_format_allowed("header_sponsorship", query)
    assert not query.allow_sponsored_fallback
    assert not placement_format_allowed("feed", query)


@pytest.mark.parametrize(
    "changes",
    [
        {"ad_formats": ["feed"]},
        {"ad_formats": ["engagement"]},
        {"ad_formats": ["sponsored_content"]},
        {"ad_formats": ["header_sponsorship", "feed"]},
        {"ad_formats": ["homepage_sponsorship"]},
        {"allow_sponsored_fallback": True},
        {"article_id": uuid.uuid4(), "topic_id": uuid.uuid4()},
        {"article_id": uuid.uuid4(), "article_ids": [uuid.uuid4()]},
        {"topic_id": uuid.uuid4(), "article_ids": [uuid.uuid4()]},
        {"article_ids": [uuid.uuid4() for _ in range(25)]},
        {"user_id": uuid.uuid4()},
    ],
)
def test_header_rejects_mixed_or_claimed_context_and_obsolete_formats(changes):
    with pytest.raises(ValidationError):
        PlacementQuery.model_validate(
            {"surface": "header", "ad_formats": ["header_sponsorship"], **changes}
        )


@pytest.mark.parametrize(
    "ad_format", ["feed", "engagement", "sponsored_content", "header_sponsorship"]
)
def test_campaign_header_scope_is_exclusive_to_header_sponsorship(ad_format):
    values = {
        "name": "Header agreement",
        "ad_format": ad_format,
        "starts_at": utcnow(),
        "ends_at": utcnow() + timedelta(days=1),
        **({"article_id": uuid.uuid4()} if ad_format == "sponsored_content" else {}),
    }
    if ad_format == "header_sponsorship":
        assert CampaignInput(**values).surfaces == ["header"]
        with pytest.raises(ValidationError):
            CampaignInput(**values, surfaces=["feed"])
    else:
        assert "header" not in CampaignInput(**values).surfaces
        with pytest.raises(ValidationError):
            CampaignInput(**values, surfaces=["header"])


@pytest.fixture
def header_catalog(database, feed_catalog):
    native_account(database, feed_catalog, ["header_sponsorship"])
    with database.begin() as session:
        session.add(
            PartnerProductPromotion(
                account_id=feed_catalog["paid"],
                product_id=feed_catalog["unrelated"],
                excluded=True,
            )
        )
    return feed_catalog


@pytest.mark.integration
@pytest.mark.parametrize("context", ["none", "article", "topic", "collection", "personal"])
def test_header_serves_one_paid_credit_and_tracks_its_independent_surface(
    database, header_catalog, context
):
    catalog = header_catalog
    values = {}
    if context in {"article", "topic"}:
        values[f"{context}_id"] = catalog[context]
    elif context == "collection":
        values["article_ids"] = [catalog["article"]]
    elif context == "personal":
        with database.begin() as session:
            session.add(UserTopic(user_id=catalog["user"], topic_id=catalog["topic"]))
    if context == "article":
        with database.begin() as session:
            session.get(Article, catalog["article"]).content_type = "article"
    rows = deliver(
        database,
        header_query(**values),
        **({"user_id": catalog["user"]} if context == "personal" else {}),
    )
    assert len(rows) == 1 and rows[0]["sponsored"]
    assert rows[0]["product_id"] == catalog["paid_product"]
    assert rows[0]["ad_format"] == "header_sponsorship"
    assert rows[0]["article"] is None
    assert rows[0]["reason"] == (
        "Sponsored by this partner" if context == "none" else "Verified support for Kubernetes."
    )
    receipt = PartnerTrackingTokens(KEY).verify(rows[0]["receipt"])
    with database.begin() as session:
        assert session.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 1
        assert session.get(PartnerPlacementDelivery, receipt.delivery_id).surface == "header"
        assert record_delivery_event(session, receipt, "impression")
        assert record_delivery_event(session, receipt, "click")


@pytest.mark.integration
def test_explicit_selected_header_campaign_serves_without_automatic_inclusion_and_cannot_bypass_cap(
    database, header_catalog
):
    catalog = header_catalog
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.auto_include_products = False
        account.promotion_kind = "product"
        campaign = PartnerCampaign(
            account_id=account.id,
            name="Selected brand agreement",
            ad_format="header_sponsorship",
            status="active",
            targeting="selected",
            product_ids=[str(catalog["paid_product"])],
            starts_at=utcnow() - timedelta(days=1),
            ends_at=utcnow() + timedelta(days=1),
            daily_impression_limit=1,
        )
        session.add(campaign)
        session.flush()
        campaign_id = campaign.id
        ensure_automatic_assets(session, account.id)
    rows = deliver(database, header_query())
    assert len(rows) == 1 and rows[0]["campaign_id"] == campaign_id
    assert deliver(database, header_query()) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "change", ["account", "format", "tier", "surface", "product", "connection"]
)
def test_header_policy_changes_reject_new_delivery_and_old_receipts(
    database, header_catalog, change
):
    catalog = header_catalog
    receipt = PartnerTrackingTokens(KEY).verify(deliver(database, header_query())[0]["receipt"])
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        if change == "account":
            account.status = "paused"
        elif change == "format":
            account.ad_formats = []
        elif change == "tier":
            account.tier = "bronze"
        elif change == "surface":
            account.promotion_surfaces = ["feed"]
        elif change == "product":
            session.get(PartnerProduct, catalog["paid_product"]).excluded = True
        elif change == "connection":
            for connection in session.scalars(
                select(PartnerConnection).where(PartnerConnection.account_id == account.id)
            ):
                connection.enabled = False
    assert deliver(database, header_query()) == []
    with database() as session, pytest.raises(InactivePartnerAsset):
        record_delivery_event(session, receipt, "impression")


@pytest.mark.integration
def test_header_rechecks_context_after_ranking_instead_of_issuing_a_stale_relevance_reason(
    database,
):
    catalog = related_topic_catalog(database)
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["account"])
        account.ad_formats = ["header_sponsorship"]
        ensure_automatic_assets(session, account.id, catalog["now"])
    with database.begin() as reader:
        with edit_before_attribution_lock(
            reader, lambda: remove_relation(database, catalog)
        ) as edits:
            rows = placements.deliver_placements(
                reader, header_query(topic_id=catalog["related"]), KEY, now=catalog["now"]
            )
        assert edits
        assert len(rows) == 1 and rows[0]["reason"] == "Sponsored by this partner"


@pytest.mark.integration
@pytest.mark.parametrize("count", [105, 205])
@pytest.mark.parametrize("rejection", ["audience", "missing_listing", "exhausted_campaign"])
def test_empty_header_inventory_is_batched_before_locks(database, count, rejection):
    catalog = seed_rotation(database, count=count)
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["account"])
        account.ad_formats = ["header_sponsorship"]
        ensure_automatic_assets(session, account.id, catalog["now"])
        campaign = session.scalar(
            select(PartnerCampaign).where(
                PartnerCampaign.account_id == account.id,
                PartnerCampaign.ad_format == "header_sponsorship",
            )
        )
        if rejection == "audience":
            account.tier = "platinum"
            account.audience = {"experience_levels": ["senior"]}
        elif rejection == "missing_listing":
            account.outbound_destination = "listing_url"
            session.execute(update(PartnerListing).values(listing_url=""))
        else:
            campaign.daily_impression_limit = 1
    if rejection == "exhausted_campaign":
        assert len(deliver(database, header_query(), now=catalog["now"])) == 1
    with database.begin() as reader:
        with counted_statements(reader) as statements:
            assert (
                placements.deliver_placements(reader, header_query(), KEY, now=catalog["now"]) == []
            )
        assert len(statements) <= (15 if count == 105 else 20), (
            f"Executed {len(statements)} SQL statements"
        )
        assert not any(statement.startswith("SAVEPOINT ") for statement in statements)
        assert_attribution_editable(database, catalog)


def topic_filtered_header_catalog(database):
    catalog = related_topic_catalog(database)
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["account"])
        account.ad_formats = []
        session.add(
            PartnerCampaign(
                account_id=account.id,
                name="Topic-filtered header agreement",
                ad_format="header_sponsorship",
                status="active",
                targeting="filters",
                topic_ids=[str(catalog["related"])],
                starts_at=catalog["now"] - timedelta(days=1),
                ends_at=catalog["now"] + timedelta(days=1),
            )
        )
        session.flush()
        ensure_automatic_assets(session, account.id, catalog["now"])
    return catalog


@pytest.mark.integration
def test_header_branding_keeps_topic_filtered_campaign_scope_current_through_issuance(
    database, monkeypatch
):
    catalog = topic_filtered_header_catalog(database)
    original = placements.account_product_creative
    observed = []

    def observe_scope_fence(*args, **kwargs):
        with database.begin() as editor:
            observed.append(
                editor.scalar(select(func.pg_try_advisory_xact_lock(TAXONOMY_LOCK_KEY)))
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(placements, "account_product_creative", observe_scope_fence)
    assert len(deliver(database, header_query(), now=catalog["now"])) == 1
    assert observed == [False]
    with database.begin() as editor:
        assert editor.scalar(select(func.pg_try_advisory_xact_lock(TAXONOMY_LOCK_KEY)))


@pytest.mark.integration
def test_header_branding_rechecks_topic_campaign_filters_after_candidate_ranking(database):
    catalog = topic_filtered_header_catalog(database)
    with database.begin() as reader:
        with edit_before_attribution_lock(
            reader, lambda: remove_relation(database, catalog)
        ) as edits:
            rows = placements.deliver_placements(reader, header_query(), KEY, now=catalog["now"])
        assert edits
        assert rows == []
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0
        assert_attribution_editable(database, catalog)


@pytest.mark.integration
@pytest.mark.parametrize("count", [105, 205])
def test_busy_header_taxonomy_skips_the_batch_without_repeated_attribution_locks(database, count):
    catalog = seed_rotation(database, count=count)
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["account"])
        account.ad_formats = ["header_sponsorship"]
        ensure_automatic_assets(session, account.id, catalog["now"])
    with database.begin() as editor:
        editor.scalar(select(func.pg_advisory_xact_lock(TAXONOMY_LOCK_KEY)))
        with database.begin() as reader:
            with counted_statements(reader) as statements:
                assert (
                    placements.deliver_placements(reader, header_query(), KEY, now=catalog["now"])
                    == []
                )
            assert sum(statement.startswith("SAVEPOINT ") for statement in statements) == 1
            assert len(statements) < 25, f"Executed {len(statements)} SQL statements"
            assert_attribution_editable(database, catalog)
