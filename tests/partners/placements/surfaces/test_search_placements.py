"""Search recommendations use verified visible collection context, never tutorial-task fallback."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import partner_placements as placements
from devfeed_core.models import (
    Article,
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductTopic,
    Source,
    Topic,
    utcnow,
)
from devfeed_core.partner_ad_formats import placement_format_allowed
from devfeed_core.partner_matching import TAXONOMY_LOCK_KEY, save_article_match
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_surfaces import default_campaign_surfaces, default_promotion_surfaces
from devfeed_core.partner_tracking import PartnerTrackingTokens, record_delivery_event
from devfeed_http.partner_placements import PlacementQuery
from devfeed_http.partner_portal import AccountInput, CampaignInput, ProductPromotionInput
from pydantic import ValidationError
from sqlalchemy import delete, event, func, select, update

from tests.partners.placements.surfaces.test_feed_placements import feed_catalog as feed_catalog
from tests.partners.placements.surfaces.test_native_formats import native_account
from tests.partners.placements.test_placement_api import make_client
from tests.partners.placements.test_placement_query_budgets import counted_statements
from tests.partners.placements.test_placement_rotation import seed_rotation
from tests.support.partner_placements import KEY, deliver
from tests.support.partner_placements import catalog as catalog


def search_query(catalog, **values):
    return PlacementQuery(
        surface="search", ad_formats=["engagement"], article_ids=[catalog["article"]], **values
    )


@pytest.mark.parametrize("count", [0, 1, 24])
def test_search_contract_accepts_only_bounded_public_article_identifiers(count):
    identifiers = [uuid.uuid4() for _ in range(count)]
    value = PlacementQuery(surface="search", ad_formats=["engagement"], article_ids=identifiers)
    assert value.article_ids == identifiers


@pytest.mark.parametrize(
    "change",
    [
        {"ad_formats": ["feed"]},
        {"ad_formats": ["header_sponsorship"]},
        {"ad_formats": ["sponsored_content"]},
        {"ad_formats": ["engagement", "feed"]},
        {"allow_sponsored_fallback": True},
        {"article_id": str(uuid.uuid4())},
        {"topic_id": str(uuid.uuid4())},
        {"article_ids": [str(uuid.uuid4())] * 25},
        {"article_ids": ["invalid"]},
        {"article_ids": None},
        {"topics": ["JavaScript"]},
    ],
)
def test_search_rejects_other_formats_fallback_and_untrusted_context(change):
    with pytest.raises(ValidationError):
        PlacementQuery.model_validate({"surface": "search", "ad_formats": ["engagement"], **change})


@pytest.mark.parametrize("private", [False, True])
def test_search_http_keeps_the_shared_private_public_and_no_store_contract(monkeypatch, private):
    state = make_client(monkeypatch, private=private)
    payload = {
        "surface": "search",
        "ad_formats": ["engagement"],
        "article_ids": [str(uuid.uuid4())],
    }
    response = state.client.post(state.path, headers=state.headers, json=payload)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert state.deliver.call_args.args[1].surface == "search"


def test_search_context_cannot_be_expanded_by_personal_interests(monkeypatch):
    topic_id = uuid.uuid4()
    session = Mock()
    context = Mock(return_value={topic_id: 80})
    interests = Mock(side_effect=AssertionError("Search must not read personal relevance weights"))
    monkeypatch.setattr(placements, "feed_topic_weights", context)
    monkeypatch.setattr(placements, "personal_topic_weights", interests)
    query = PlacementQuery(surface="search", article_ids=[uuid.uuid4()], ad_formats=["engagement"])
    statement = placements.contextual_candidates(
        session, query, utcnow(), uuid.uuid4(), as_query=True
    )
    assert statement is not None
    interests.assert_not_called()
    context.return_value = {}
    assert (
        placements.contextual_candidates(session, query, utcnow(), uuid.uuid4(), as_query=True)
        is None
    )
    assert placement_format_allowed("engagement", query)
    assert not placement_format_allowed(
        "feed", SimpleNamespace(surface="search", ad_formats=["feed"])
    )


def test_surface_defaults_keep_search_exclusive_to_engagement_campaigns():
    full = ["article", "topic", "feed", "search", "header"]
    assert default_promotion_surfaces() == full
    assert AccountInput(name="Partner", tier="gold").promotion_surfaces == full
    assert ProductPromotionInput(promotion_surfaces=["search"]).promotion_surfaces == ["search"]
    for ad_format, expected in [
        ("feed", full[:3]),
        ("engagement", full[:4]),
        ("header_sponsorship", ["header"]),
    ]:
        values = dict(
            name="Campaign",
            ad_format=ad_format,
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=1),
        )
        assert default_campaign_surfaces(ad_format) == expected
        assert CampaignInput(**values).surfaces == expected
        if ad_format != "engagement":
            with pytest.raises(ValidationError):
                CampaignInput(**values, surfaces=["search"])


@pytest.fixture
def search_catalog(database, feed_catalog):
    native_account(database, feed_catalog, ["engagement"])
    return feed_catalog


@pytest.mark.integration
@pytest.mark.parametrize("content_type", ["news", "tutorial"])
@pytest.mark.parametrize("role", ["primary", "supporting"])
def test_direct_search_context_serves_even_when_the_individual_tutorial_task_is_rejected(
    database, search_catalog, content_type, role
):
    catalog = search_catalog
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        article = session.get(Article, catalog["article"])
        assert save_article_match(
            session, product, article, False, 0, "Not this tutorial task.", origin="evaluation"
        )
        article.content_type = content_type
        session.get(ArticleTopic, (article.id, catalog["topic"])).role = role
        assert session.get(PartnerProductTopic, (product.id, catalog["topic"])).score == 100
    rows = deliver(database, search_query(catalog))
    assert len(rows) == 1 and rows[0]["product_id"] == catalog["paid_product"]
    assert (
        rows[0]["ad_format"] == "engagement"
        and rows[0]["reason"] == "Verified support for Kubernetes."
    )
    assert (
        deliver(
            database,
            PlacementQuery(
                surface="feed", article_ids=[catalog["article"]], ad_formats=["engagement"]
            ),
        )
        == []
    )
    assert (
        deliver(
            database,
            PlacementQuery(
                surface="article", article_id=catalog["article"], ad_formats=["engagement"]
            ),
        )
        == []
    )
    receipt = PartnerTrackingTokens(KEY).verify(rows[0]["receipt"])
    with database.begin() as session:
        assert session.get(PartnerPlacementDelivery, receipt.delivery_id).surface == "search"
        assert record_delivery_event(session, receipt, "impression")
        assert record_delivery_event(session, receipt, "click")


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        "weak",
        "almost_direct",
        "rejected_topic",
        "private_source",
        "unpublished",
        "incidental",
        "comparison",
        "stale_product",
        "stale_topic",
        "weak_context",
    ],
)
def test_search_requires_current_direct_support_and_approved_visible_topic_context(
    database, search_catalog, change
):
    catalog = search_catalog
    with database.begin() as session:
        edge = session.get(PartnerProductTopic, (catalog["paid_product"], catalog["topic"]))
        if change in {"weak", "almost_direct"}:
            edge.score = 65 if change == "weak" else 99
        elif change == "rejected_topic":
            session.get(Topic, catalog["topic"]).status = "rejected"
        elif change == "private_source":
            session.get(Source, catalog["source"]).approval_status = "pending"
        elif change == "unpublished":
            session.get(Article, catalog["article"]).publication_status = "unpublished"
        elif change in {"incidental", "comparison"}:
            session.get(ArticleTopic, (catalog["article"], catalog["topic"])).role = change
        elif change == "stale_product":
            edge.product_revision += 1
        elif change == "stale_topic":
            edge.topic_updated_at -= timedelta(seconds=1)
        else:
            session.get(ArticleTopic, (catalog["article"], catalog["topic"])).relevance = 0.39
    assert deliver(database, search_query(catalog)) == []


@pytest.mark.integration
def test_search_without_visible_context_never_uses_a_readers_personal_feed(
    database, search_catalog
):
    catalog = search_catalog
    for identifiers in ([], [uuid.uuid4()]):
        query = PlacementQuery(surface="search", article_ids=identifiers, ad_formats=["engagement"])
        with database.begin() as session:
            assert placements.deliver_placements(session, query, KEY, user_id=catalog["user"]) == []


@pytest.mark.integration
def test_search_reserves_only_one_delivery_when_both_paid_and_ordinary_products_qualify(
    database, search_catalog
):
    catalog = search_catalog
    with database.begin() as session:
        free = session.get(PartnerAccount, catalog["free"])
        free.tier = "silver"
        free.ad_formats = ["engagement"]
        session.add(
            PartnerAsset(
                account_id=free.id,
                product_id=catalog["free_product"],
                name="Ordinary Engagement",
                kind="product",
                ad_format="engagement",
                automatic=True,
                status="active",
            )
        )
    rows = deliver(database, search_query(catalog))
    assert (
        len(rows) == 1 and rows[0]["sponsored"] and rows[0]["product_id"] == catalog["paid_product"]
    )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 1


@pytest.mark.integration
@pytest.mark.parametrize("change", ["delete", "weaken"])
def test_search_rechecks_direct_relevance_after_candidate_ranking_before_issuance(
    database, search_catalog, change
):
    catalog = search_catalog
    edited = False

    def invalidate(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal edited
        if edited or "FROM partner_products" not in statement or "FOR UPDATE" not in statement:
            return
        edited = True
        with database.begin() as editor:
            if change == "delete":
                editor.execute(
                    delete(PartnerProductTopic).where(
                        PartnerProductTopic.product_id == catalog["paid_product"]
                    )
                )
            else:
                editor.get(
                    PartnerProductTopic, (catalog["paid_product"], catalog["topic"])
                ).score = 65

    with database.begin() as reader:
        connection = reader.connection()
        event.listen(connection, "before_cursor_execute", invalidate)
        try:
            assert placements.deliver_placements(reader, search_query(catalog), KEY) == []
        finally:
            event.remove(connection, "before_cursor_execute", invalidate)
        assert edited
        assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0


@pytest.mark.integration
def test_search_respects_the_final_taxonomy_publication_barrier(database, search_catalog):
    with database.begin() as editor:
        editor.execute(select(func.pg_advisory_xact_lock(TAXONOMY_LOCK_KEY)))
        with database.begin() as reader:
            with counted_statements(reader) as statements:
                assert (
                    placements.deliver_placements(reader, search_query(search_catalog), KEY) == []
                )
            assert len(statements) <= 15
            assert reader.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0


@pytest.mark.integration
@pytest.mark.parametrize("count", [105, 205])
@pytest.mark.parametrize("rejection", ["weak", "audience", "exhausted_campaign", "missing_listing"])
def test_empty_search_results_have_bounded_sql_at_large_catalog_sizes(database, count, rejection):
    catalog = seed_rotation(database, count=count)
    with database.begin() as session:
        session.add(
            ArticleTopic(
                article_id=catalog["article"],
                topic_id=catalog["topic"],
                role="primary",
                relevance=1.0,
                evidence="Python is the primary context.",
            )
        )
        session.execute(
            update(PartnerProductTopic).values(score=65 if rejection == "weak" else 100)
        )
        account = session.get(PartnerAccount, catalog["account"])
        account.tier = "platinum"
        account.ad_formats = ["engagement"]
        if rejection == "audience":
            account.audience = {"experience_levels": ["senior"]}
        if rejection == "missing_listing":
            account.outbound_destination = "listing_url"
            session.execute(update(PartnerListing).values(listing_url=""))
        ensure_automatic_assets(session, account.id, now=catalog["now"])
        if rejection == "exhausted_campaign":
            campaign = session.scalar(
                select(PartnerCampaign).where(
                    PartnerCampaign.account_id == account.id,
                    PartnerCampaign.ad_format == "engagement",
                )
            )
            campaign.daily_impression_limit = 1
            session.flush()
            asset = session.scalar(
                select(PartnerAsset).where(PartnerAsset.campaign_id == campaign.id)
            )
            session.add(
                PartnerPlacementDelivery(
                    delivery_id=uuid.uuid4(),
                    asset_id=asset.id,
                    issued_at=catalog["now"],
                    surface="search",
                )
            )
    with database.begin() as reader:
        with counted_statements(reader) as statements:
            rows = placements.deliver_placements(
                reader, search_query(catalog), KEY, now=catalog["now"]
            )
        assert rows == []
        assert len(statements) <= 15, f"Executed {len(statements)} SQL statements"
