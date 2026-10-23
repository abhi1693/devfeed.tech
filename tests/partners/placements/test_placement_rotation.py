"""Equal eligible products rotate without widening relevance or campaign budgets."""

import secrets
import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    PartnerAccount,
    PartnerArticleMatch,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductTopic,
    Source,
    Topic,
    utcnow,
)
from devfeed_core.partner_matching import article_fingerprint
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import select

pytestmark = pytest.mark.integration
KEY = secrets.token_urlsafe(32)


def seed_rotation(database, count=3):
    now = utcnow()
    with database.begin() as session:
        account = PartnerAccount(name="Rotating sponsor", tier="silver")
        topic = Topic(name="Python", slug="python", kind="language", status="active")
        source = Source(
            name="Publisher",
            feed_url="https://publisher.example/feed",
            source_type="publisher",
            approval_status="approved",
        )
        session.add_all([account, topic, source])
        session.flush()
        session.add(PartnerConnection(provider="rotation", account_id=account.id, enabled=True))
        products = [
            PartnerProduct(
                id=uuid.UUID(int=1000 + i),
                name=f"Product {i}",
                product_url=f"https://product{i}.example/",
                tagline="Verified Python tooling",
                technologies=["Python"],
                status="approved",
                verified_at=now,
            )
            for i in range(count)
        ]
        session.add_all(products)
        article = Article(
            canonical_url="https://publisher.example/python-tutorial",
            url_hash=uuid.uuid4().hex,
            title="Python testing tutorial",
            summary="A practical Python testing tutorial.",
            content_type="tutorial",
            review_status="approved",
            publication_status="published",
        )
        session.add(article)
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="tutorial",
                original_url=article.canonical_url,
            )
        )
        for product in products:
            session.add(
                PartnerListing(
                    provider="rotation",
                    external_id=str(product.id),
                    product_id=product.id,
                    name=product.name,
                    tagline=product.tagline,
                    product_url=product.product_url,
                    listing_url=f"https://catalog.example/products/{product.id}",
                )
            )
            session.add(
                PartnerProductTopic(
                    product_id=product.id,
                    topic_id=topic.id,
                    product_revision=product.revision,
                    product_updated_at=product.updated_at,
                    topic_updated_at=topic.updated_at,
                    score=80,
                    reason="Verified Python support",
                    updated_at=now,
                )
            )
            session.add(
                PartnerArticleMatch(
                    product_id=product.id,
                    article_id=article.id,
                    product_revision=product.revision,
                    product_updated_at=product.updated_at,
                    editorial_revision=article.editorial_revision,
                    article_fingerprint=article_fingerprint(article),
                    relevant=True,
                    score=85,
                    reason="Grounded Python testing",
                    updated_at=now,
                )
            )
        session.flush()
        ensure_automatic_assets(session, account.id, now)
        return dict(
            now=now,
            account=account.id,
            topic=topic.id,
            article=article.id,
            products=[product.id for product in products],
        )


def issue(database, catalog, *, surface="feed", now=None):
    query = PlacementQuery(
        surface=surface,
        **(
            {"allow_sponsored_fallback": True}
            if surface == "feed"
            else {"topic_id": catalog["topic"]}
            if surface == "topic"
            else {"article_id": catalog["article"]}
        ),
    )
    with database.begin() as session:
        return deliver_placements(session, query, KEY, now=now or catalog["now"])


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
def test_refreshes_rotate_equal_products_without_a_reader_exposure_cap(database, surface):
    catalog = seed_rotation(database)
    observed = []
    for index in range(12):
        rows = issue(
            database, catalog, surface=surface, now=catalog["now"] + timedelta(seconds=index)
        )
        assert len(rows) == 1
        observed.append(rows[0]["product_id"])
    assert observed == catalog["products"] * 4
    with database() as session:
        assert all(
            campaign.daily_impression_limit is None
            for campaign in session.scalars(select(PartnerCampaign))
        )


def mark_served(database, catalog, product_ids):
    with database.begin() as session:
        assets = session.scalars(
            select(PartnerAsset).where(PartnerAsset.product_id.in_(product_ids))
        ).all()
        session.add_all(
            [
                PartnerPlacementDelivery(
                    delivery_id=uuid.uuid4(),
                    asset_id=asset.id,
                    issued_at=catalog["now"] - timedelta(minutes=1),
                    surface="feed",
                )
                for asset in assets
            ]
        )


@pytest.mark.parametrize("surface,count", [("feed", 105), ("article", 105), ("topic", 1005)])
def test_rotation_happens_before_bounded_candidate_cutoffs(database, surface, count):
    catalog = seed_rotation(database, count=count)
    mark_served(database, catalog, catalog["products"][:-1])
    rows = issue(database, catalog, surface=surface)
    assert len(rows) == 1
    assert rows[0]["product_id"] == catalog["products"][-1]


def test_stronger_context_remains_ahead_of_an_unserved_weaker_product(database):
    catalog = seed_rotation(database, count=2)
    with database.begin() as session:
        session.get(PartnerProductTopic, (catalog["products"][0], catalog["topic"])).score = 95
    mark_served(database, catalog, [catalog["products"][0]])
    assert issue(database, catalog, surface="topic")[0]["product_id"] == catalog["products"][0]


def test_campaign_priority_precedes_rotation_and_real_budget_still_wins(database):
    catalog = seed_rotation(database, count=2)
    with database.begin() as session:
        high = PartnerCampaign(
            account_id=catalog["account"],
            name="Priority agreement",
            targeting="selected",
            product_ids=[str(catalog["products"][0])],
            status="active",
            priority=90,
            starts_at=catalog["now"] - timedelta(days=1),
            ends_at=catalog["now"] + timedelta(days=1),
        )
        session.add(high)
        session.flush()
        ensure_automatic_assets(session, catalog["account"], catalog["now"])
        high_id = high.id
    first = issue(database, catalog, surface="topic")
    second = issue(database, catalog, surface="topic", now=catalog["now"] + timedelta(seconds=1))
    assert first[0]["product_id"] == second[0]["product_id"] == catalog["products"][0]
    with database.begin() as session:
        session.get(PartnerCampaign, high_id).daily_impression_limit = 2
    third = issue(database, catalog, surface="topic", now=catalog["now"] + timedelta(seconds=2))
    assert third[0]["product_id"] == catalog["products"][1]
    assert third[0]["campaign_id"] != high_id


def test_rotated_candidate_still_obeys_live_global_exclusion(database):
    catalog = seed_rotation(database, count=2)
    mark_served(database, catalog, [catalog["products"][0]])
    with database.begin() as session:
        session.get(PartnerProduct, catalog["products"][1]).excluded = True
    assert issue(database, catalog)[0]["product_id"] == catalog["products"][0]


def test_every_equally_eligible_fallback_product_gets_a_turn_beyond_first_hundred(database):
    catalog = seed_rotation(database, count=105)
    observed = [
        issue(database, catalog, now=catalog["now"] + timedelta(seconds=index))[0]["product_id"]
        for index in range(105)
    ]
    assert observed == catalog["products"]
    assert (
        issue(database, catalog, now=catalog["now"] + timedelta(seconds=105))[0]["product_id"]
        == catalog["products"][0]
    )
