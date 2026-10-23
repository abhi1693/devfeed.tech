"""Signing in preserves contextual inventory without bypassing commercial policy."""

from datetime import timedelta

import pytest
from devfeed_core.models import (
    ArticleTopic,
    PartnerCampaign,
    UserAccount,
    UserInterest,
    UserRecommendationState,
    UserTopic,
    utcnow,
)
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tracking import PartnerTrackingTokens
from devfeed_http.partner_placements import PlacementQuery

from tests.partners.placements.surfaces.test_feed_placements import feed_catalog as feed_catalog
from tests.partners.placements.surfaces.test_native_formats import sponsored_article
from tests.support.partner_placements import KEY, deliver
from tests.support.partner_placements import catalog as catalog

pytestmark = pytest.mark.integration

PAGE_PLACEMENTS = [
    pytest.param("feed", "feed", "collection", id="feed-grid-or-table"),
    pytest.param("feed", "topic", "topic", id="topic-feed"),
    pytest.param("engagement", "feed", "collection", id="feed-engagement"),
    pytest.param("engagement", "article", "article", id="article-sidebar"),
    pytest.param("engagement", "topic", "topic", id="topic-sidebar"),
    pytest.param("engagement", "search", "collection", id="search-sidebar"),
    pytest.param("header_sponsorship", "header", "none", id="global-header"),
    pytest.param("header_sponsorship", "header", "article", id="article-header"),
    pytest.param("header_sponsorship", "header", "topic", id="topic-header"),
    pytest.param("header_sponsorship", "header", "collection", id="feed-header"),
    pytest.param("sponsored_content", "feed", "collection", id="sponsored-article"),
]


def page_query(catalog, ad_format, surface, context):
    values = (
        {"article_ids": [catalog["article"]]}
        if context == "collection"
        else {f"{context}_id": catalog[context]}
        if context in {"article", "topic"}
        else {}
    )
    return PlacementQuery(surface=surface, ad_formats=[ad_format], **values)


def configure_campaign(database, catalog, ad_format, surface):
    story = sponsored_article(database, catalog) if ad_format == "sponsored_content" else None
    with database.begin() as session:
        campaign = session.get(PartnerCampaign, catalog["campaign"])
        campaign.ad_format = ad_format
        campaign.article_id = story
        campaign.targeting = "selected"
        campaign.product_ids = [str(catalog["paid_product"])]
        campaign.surfaces = [surface]
        ensure_automatic_assets(session, catalog["paid"])
    return story


def add_unrelated_interests(database, catalog):
    with database.begin() as session:
        session.add_all(
            [
                UserTopic(user_id=catalog["user"], topic_id=catalog["other_topic"]),
                UserInterest(
                    user_id=catalog["user"],
                    topic_id=catalog["other_topic"],
                    seed_topic_id=catalog["other_topic"],
                    weight=100,
                    reason="liked_topic",
                ),
            ]
        )
        session.flush()
        generation = session.get(UserRecommendationState, catalog["user"])
        generation.invalidated = False
        generation.computed_at = utcnow()
        generation.expires_at = utcnow() + timedelta(hours=1)


@pytest.mark.parametrize("ad_format,surface,context", PAGE_PLACEMENTS)
@pytest.mark.parametrize("interests", ["empty", "unrelated"])
def test_anonymous_page_inventory_remains_eligible_after_sign_in(
    database, feed_catalog, ad_format, surface, context, interests
):
    story = configure_campaign(database, feed_catalog, ad_format, surface)
    if interests == "unrelated":
        add_unrelated_interests(database, feed_catalog)
    payload = page_query(feed_catalog, ad_format, surface, context)

    anonymous = deliver(database, payload)
    signed_in = deliver(database, payload, user_id=feed_catalog["user"])
    expected_products = {feed_catalog["paid_product"]}
    if ad_format == "feed":
        expected_products.add(feed_catalog["free_product"])
    for items in (anonymous, signed_in):
        assert {item["product_id"] for item in items} == expected_products
        assert {item["ad_format"] for item in items} == {ad_format}
        paid = next(item for item in items if item["sponsored"])
        assert paid["product_id"] == feed_catalog["paid_product"]
        assert paid["campaign_id"] == feed_catalog["campaign"]
        assert (paid["article"].id if paid["article"] else None) == story
        receipt = PartnerTrackingTokens(KEY).verify(paid["receipt"])
        assert receipt.asset_id == paid["asset_id"]
    assert {item["receipt"] for item in anonymous}.isdisjoint(item["receipt"] for item in signed_in)

    # Both identities reserve the same funded capacity; sign-in cannot reset its cap.
    with database.begin() as session:
        session.get(PartnerCampaign, feed_catalog["campaign"]).daily_impression_limit = 2
    for identity in ({}, {"user_id": feed_catalog["user"]}):
        assert not any(item["sponsored"] for item in deliver(database, payload, **identity))


@pytest.mark.parametrize(
    "ad_format", ["feed", "engagement", "header_sponsorship", "sponsored_content"]
)
def test_sign_in_parity_does_not_bypass_targeted_audiences(database, feed_catalog, ad_format):
    surface = "header" if ad_format == "header_sponsorship" else "feed"
    configure_campaign(database, feed_catalog, ad_format, surface)
    payload = page_query(feed_catalog, ad_format, surface, "collection")
    with database.begin() as session:
        session.get(PartnerCampaign, feed_catalog["campaign"]).audience = {
            "experience_levels": ["senior"]
        }
        session.get(UserAccount, feed_catalog["user"]).profile = {"experience_level": "entry_level"}
    assert not any(item["sponsored"] for item in deliver(database, payload))
    assert not any(
        item["sponsored"] for item in deliver(database, payload, user_id=feed_catalog["user"])
    )
    with database.begin() as session:
        session.get(UserAccount, feed_catalog["user"]).profile = {"experience_level": "senior"}
    items = deliver(database, payload, user_id=feed_catalog["user"])
    assert {item["product_id"] for item in items if item["sponsored"]} == {
        feed_catalog["paid_product"]
    }


@pytest.mark.parametrize("surface", ["topic", "search"])
def test_sign_in_cannot_turn_unrelated_interests_into_page_relevance(
    database, feed_catalog, surface
):
    configure_campaign(database, feed_catalog, "engagement", surface)
    with database.begin() as session:
        # Follow the product's topic while viewing an unrelated topic or search result set.
        session.add(UserTopic(user_id=feed_catalog["user"], topic_id=feed_catalog["topic"]))
        session.get(
            ArticleTopic, (feed_catalog["article"], feed_catalog["topic"])
        ).topic_id = feed_catalog["other_topic"]
    payload = (
        PlacementQuery(
            surface="topic", topic_id=feed_catalog["other_topic"], ad_formats=["engagement"]
        )
        if surface == "topic"
        else PlacementQuery(
            surface="search", article_ids=[feed_catalog["article"]], ad_formats=["engagement"]
        )
    )
    assert deliver(database, payload) == []
    assert deliver(database, payload, user_id=feed_catalog["user"]) == []
