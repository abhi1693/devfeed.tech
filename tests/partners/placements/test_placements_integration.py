"""Real inherited free/paid delivery, durable matching, caps and policy fences."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    PartnerAccount,
    PartnerArticleMatch,
    PartnerAsset,
    PartnerCampaign,
    PartnerDeliveryEvent,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductPromotion,
    Topic,
    UserInterest,
    UserRecommendationState,
    UserTopic,
    utcnow,
)
from devfeed_core.partner_matching import article_match, refresh_partner_matches, save_article_match
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tracking import (
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import delete, func, select

from tests.support.partner_placements import (
    KEY as KEY,
)
from tests.support.partner_placements import (
    catalog as catalog,
)
from tests.support.partner_placements import (
    deliver as deliver,
)
from tests.support.partner_placements import (
    finish_matching as finish_matching,
)
from tests.support.partner_placements import (
    query as query,
)

pytestmark = pytest.mark.integration


def test_free_and_paid_relevant_products_automatically_deliver_and_track(client, database, catalog):
    response = client.post(
        "/v1/partner-placements/query", json=query(catalog).model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert [(item["name"], item["sponsored"]) for item in items] == [
        ("free_product", False),
        ("paid_product", True),
    ]
    assert items[1]["campaign_id"] == str(catalog["campaign"])
    for item in items:
        for _ in range(2):
            assert (
                client.post(
                    "/v1/partner-tracking/events",
                    json={"receipt": item["receipt"], "kind": "impression"},
                ).status_code
                == 204
            )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerDeliveryEvent)) == 2
        assert session.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 2
    assert (
        client.post(
            "/v1/partner-placements/query",
            json={"surface": "feed", "user_id": str(catalog["user"])},
        ).status_code
        == 422
    )
    assert client.post("/v1/partner-placements/query", content="x" * 16385).status_code == 413


def test_topics_user_interests_and_anonymous_absence_of_context(database, catalog):
    assert {item["product_id"] for item in deliver(database, query(catalog, "topic"))} == {
        catalog["free_product"],
        catalog["paid_product"],
    }
    assert deliver(database, query(catalog, "feed")) == []
    with database.begin() as session:
        session.add(UserTopic(user_id=catalog["user"], topic_id=catalog["topic"]))
    assert len(deliver(database, query(catalog, "feed"), user_id=catalog["user"])) == 2
    with database.begin() as session:
        session.execute(delete(UserTopic).where(UserTopic.user_id == catalog["user"]))
        session.add(
            UserInterest(
                user_id=catalog["user"],
                topic_id=catalog["topic"],
                seed_topic_id=catalog["topic"],
                weight=60,
                reason="liked_topic",
            )
        )
        state = session.get(UserRecommendationState, catalog["user"])
        if state is None:
            state = UserRecommendationState(user_id=catalog["user"])
            session.add(state)
        state.invalidated, state.computed_at = False, utcnow()
    assert len(deliver(database, query(catalog, "feed"), user_id=catalog["user"])) == 2
    with database.begin() as session:
        session.get(UserRecommendationState, catalog["user"]).invalidated = True
    assert deliver(database, query(catalog, "feed"), user_id=catalog["user"]) == []


@pytest.mark.parametrize(
    "change",
    [
        "pause_account",
        "pause_campaign",
        "exclude",
        "withdraw_listing",
        "stale_product",
        "account_surface",
        "campaign_surface",
        "expired",
    ],
)
def test_policy_changes_fence_both_new_delivery_and_existing_receipts(database, catalog, change):
    original = next(item for item in deliver(database, query(catalog)) if item["sponsored"])
    with database.begin() as session:
        if change == "pause_account":
            session.get(PartnerAccount, catalog["paid"]).status = "paused"
        elif change == "pause_campaign":
            session.get(PartnerCampaign, catalog["campaign"]).status = "paused"
        elif change == "exclude":
            session.add(
                PartnerProductPromotion(
                    account_id=catalog["paid"], product_id=catalog["paid_product"], excluded=True
                )
            )
        elif change == "withdraw_listing":
            session.scalar(
                select(PartnerListing).where(PartnerListing.product_id == catalog["paid_product"])
            ).active = False
        elif change == "stale_product":
            session.get(PartnerProduct, catalog["paid_product"]).verified_at = utcnow() - timedelta(
                days=91
            )
        elif change == "account_surface":
            session.get(PartnerAccount, catalog["paid"]).promotion_surfaces = ["topic"]
        elif change == "campaign_surface":
            session.get(PartnerCampaign, catalog["campaign"]).surfaces = ["topic"]
        elif change == "expired":
            session.get(PartnerCampaign, catalog["campaign"]).ends_at = utcnow() - timedelta(
                seconds=1
            )
    assert not any(item["sponsored"] for item in deliver(database, query(catalog)))
    # Surface policy is evaluated at issuance; receipts intentionally contain no reader context.
    if change not in {"account_surface", "campaign_surface"}:
        with database.begin() as session, pytest.raises(InactivePartnerAsset):
            record_delivery_event(
                session, PartnerTrackingTokens(KEY).verify(original["receipt"]), "impression"
            )


def test_campaign_expiry_retains_organic_eligibility_and_history(database, catalog):
    with database.begin() as session:
        session.get(PartnerCampaign, catalog["campaign"]).ends_at = utcnow() - timedelta(seconds=1)
    items = deliver(database, query(catalog, exclude_product_ids=[catalog["free_product"]]))
    assert (
        len(items) == 1
        and not items[0]["sponsored"]
        and items[0]["product_id"] == catalog["paid_product"]
    )
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerAsset)
                .where(PartnerAsset.campaign_id == catalog["campaign"])
            )
            == 2
        )


@pytest.mark.parametrize(
    ("hour", "minute"),
    [(0, 5), (12, 0), (23, 30)],
    ids=["after-midnight", "noon", "before-midnight"],
)
def test_concurrent_campaign_reservations_never_exceed_shared_limit(
    database, catalog, hour, minute
):
    issued_at = utcnow().replace(hour=hour, minute=minute, second=0, microsecond=0)

    def prepare_context(now):
        with database.begin() as session:
            article = session.get(Article, catalog["article"])
            for name in ("free_product", "paid_product"):
                product = session.get(PartnerProduct, catalog[name])
                prepared = article_match(product, article)
                assert prepared[0]
                assert save_article_match(session, product, article, *prepared, now=now)

    with database.begin() as session:
        campaign = session.get(PartnerCampaign, catalog["campaign"])
        campaign.daily_impression_limit = 1
        campaign.starts_at = issued_at - timedelta(days=1)
        campaign.ends_at = issued_at + timedelta(days=1)
    prepare_context(issued_at)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(lambda _: deliver(database, query(catalog), now=issued_at), range(4)))
    assert sum(item["sponsored"] for page in pages for item in page) == 1
    funded = next(item for page in pages for item in page if item["sponsored"])
    with database.begin() as session:
        receipt = PartnerTrackingTokens(KEY, clock=lambda: issued_at.timestamp()).verify(
            funded["receipt"]
        )
        assert receipt is not None
        assert record_delivery_event(session, receipt, "impression")
    assert not any(item["sponsored"] for item in deliver(database, query(catalog), now=issued_at))
    after_expiry = issued_at + timedelta(hours=2)
    if after_expiry.date() != issued_at.date():
        before_expiry = issued_at + timedelta(minutes=35)
        prepare_context(before_expiry)
        assert not any(
            item["sponsored"] for item in deliver(database, query(catalog), now=before_expiry)
        )
    prepare_context(after_expiry)
    # Expired receipts retain measured usage for their UTC day; the next day resets the cap.
    assert any(
        item["sponsored"] for item in deliver(database, query(catalog), now=after_expiry)
    ) == (after_expiry.date() != issued_at.date())
    # A higher cap must unblock the same eligible context, independently of match freshness.
    with database.begin() as session:
        session.get(PartnerCampaign, catalog["campaign"]).daily_impression_limit = 2
    assert any(item["sponsored"] for item in deliver(database, query(catalog), now=after_expiry))


def test_article_edits_invalidate_matches_and_durable_jobs_rebuild(database, catalog):
    with database.begin() as session:
        item = session.get(Article, catalog["article"])
        item.summary = "Deploy Kubernetes applications with namespace configuration and manifests."
    assert deliver(database, query(catalog)) == []
    refresh_partner_matches(database)
    assert deliver(database, query(catalog)) == []
    with database.begin() as session:
        item = session.get(Article, catalog["article"])
        item.summary = (
            "Monitor Kubernetes clusters and inspect metrics in this practical production tutorial."
        )
    finish_matching(database, article_limit=1)
    assert len(deliver(database, query(catalog))) == 2


def test_current_evaluator_negatives_override_lexical_match_until_inputs_change(database, catalog):
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        item = session.get(Article, catalog["article"])
        save_article_match(
            session,
            product,
            item,
            False,
            0,
            "Verified evaluator found the task incompatible.",
            origin="evaluation",
        )
        session.get(Topic, catalog["topic"]).aliases = ["K8s"]
    refresh_partner_matches(database)
    assert [item["name"] for item in deliver(database, query(catalog))] == ["free_product"]
    with database() as session:
        assert (
            session.get(PartnerArticleMatch, (catalog["paid_product"], catalog["article"])).origin
            == "evaluation"
        )


def test_later_imports_gain_defaults_and_offline_matching_without_manual_assets(database, catalog):
    with database.begin() as session:
        source = session.get(PartnerProduct, catalog["paid_product"])
        product = PartnerProduct(
            name="New tool",
            product_url="https://tools.example/new",
            tagline=source.tagline,
            technologies=source.technologies,
            evidence=source.evidence,
            status="approved",
            verified_at=utcnow(),
        )
        session.add(product)
        session.flush()
        identifier = product.id
        session.add(
            PartnerListing(
                product_id=identifier,
                provider="paid",
                external_id="new",
                name="New tool",
                product_url=product.product_url,
                listing_url="https://catalog.example/new",
                tagline="A newly approved listing.",
            )
        )
    refresh_partner_matches(database)
    items = deliver(
        database,
        query(catalog, exclude_product_ids=[catalog["free_product"], catalog["paid_product"]]),
    )
    assert len(items) == 1 and items[0]["product_id"] == identifier and items[0]["sponsored"]


def test_requalification_invalidates_prepared_edges_without_a_revision_bump(database, catalog):
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        revision = product.revision
        product.technologies = ["Figma"]
        product.evidence = [
            {
                "quote": "Design Figma icons and export vector illustrations for your team.",
                "capability": "Design Figma icons and export vector illustrations",
            }
        ]
        product.updated_at = utcnow()
    assert not any(item["sponsored"] for item in deliver(database, query(catalog)))
    assert not any(item["sponsored"] for item in deliver(database, query(catalog, "topic")))
    refresh_partner_matches(database)
    assert not any(item["sponsored"] for item in deliver(database, query(catalog)))
    with database() as session:
        assert session.get(PartnerProduct, catalog["paid_product"]).revision == revision


def test_stale_products_cannot_starve_fresh_matching_batches(database, catalog):
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        product.updated_at = utcnow()
        fresh_timestamp = product.updated_at
        for index in range(12):
            product = PartnerProduct(
                id=uuid.UUID(int=index + 1),
                name=f"Stale {index}",
                product_url=f"https://tools.example/stale-{index}",
                tagline="Stale tool.",
                status="approved",
                verified_at=utcnow() - timedelta(days=91),
            )
            session.add(product)
            session.flush()
            session.add(
                PartnerListing(
                    product_id=product.id,
                    provider="free",
                    external_id=f"stale-{index}",
                    name=product.name,
                    product_url=product.product_url,
                    listing_url=f"https://catalog.example/stale-{index}",
                    tagline="Old listing.",
                )
            )
    finish_matching(database, product_limit=1)
    with database() as session:
        match = session.get(PartnerArticleMatch, (catalog["paid_product"], catalog["article"]))
        assert match.relevant and match.product_updated_at == fresh_timestamp
        assert not session.scalar(
            select(func.count())
            .select_from(PartnerArticleMatch)
            .where(PartnerArticleMatch.product_id.in_([uuid.UUID(int=i + 1) for i in range(12)]))
        )


def test_new_low_uuid_articles_are_prepared_for_existing_products(database, catalog):
    article_id = uuid.UUID(int=1)
    with database.begin() as session:
        original = session.get(Article, catalog["article"])
        item = Article(
            id=article_id,
            canonical_url="https://publisher.example/new-tutorial",
            url_hash=uuid.uuid4().hex,
            title=original.title,
            summary=original.summary,
            content_type="tutorial",
            review_status="approved",
            publication_status="published",
        )
        session.add(item)
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=item.id,
                source_id=catalog["source"],
                entry_key="new",
                original_url=item.canonical_url,
            )
        )
    finish_matching(database, article_limit=1)
    assert len(deliver(database, PlacementQuery(surface="article", article_id=article_id))) == 2


def test_relevance_precedes_commercial_priority(database, catalog):
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["free"])
        account.tier, account.promotion_kind = "silver", "product"
        campaign = session.get(PartnerCampaign, catalog["campaign"])
        campaign.priority = 100
        campaign.targeting = "selected"
        campaign.product_ids = [str(catalog["paid_product"])]
        session.get(PartnerArticleMatch, (catalog["paid_product"], catalog["article"])).score = 75
        session.add(
            PartnerCampaign(
                account_id=catalog["free"],
                name="Stronger relevant sponsor",
                status="active",
                targeting="selected",
                product_ids=[str(catalog["free_product"])],
                priority=1,
                starts_at=utcnow() - timedelta(days=1),
                ends_at=utcnow() + timedelta(days=1),
            )
        )
        session.flush()
        ensure_automatic_assets(session)
    items = deliver(database, query(catalog))
    sponsored = next(item for item in items if item["sponsored"])
    assert sponsored["product_id"] == catalog["free_product"]
    assert len({item["product_id"] for item in items}) == len(items)
