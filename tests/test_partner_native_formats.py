"""Native creatives keep relevance, immutable attribution and commercial policy fences."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerPlacementDelivery,
    PartnerProduct,
    utcnow,
)
from devfeed_core.partner_ad_formats import ad_format_allowed, validate_account_formats
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tiers import tier_policy
from devfeed_core.partner_tracking import (
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from devfeed_http.partner_placements import PlacementQuery
from devfeed_http.partner_portal import CampaignInput, reporting_dashboard
from pydantic import ValidationError
from sqlalchemy import select
from test_partner_feed_placements import feed_catalog as feed_catalog
from test_partner_placements_integration import KEY, deliver
from test_partner_placements_integration import catalog as catalog
from test_partner_portal import admin_portal_client as admin_portal_client

FORMATS = ["feed", "header_sponsorship", "sponsored_content", "engagement"]


@pytest.mark.parametrize("tier", ["bronze", "silver", "gold", "platinum", "diamond"])
def test_native_format_entitlements_start_at_silver_without_expanding_audience(tier):
    account = PartnerAccount(tier=tier)
    assert [kind for kind in FORMATS if ad_format_allowed(account, kind)] == (
        ["feed"] if tier == "bronze" else FORMATS
    )
    assert tier_policy(tier)["advanced_targeting"] == (tier in {"platinum", "diamond"})
    assert validate_account_formats(tier, []) is None
    with pytest.raises(ValueError):
        validate_account_formats(tier, ["sponsored_content"])


@pytest.mark.parametrize(
    "payload",
    [
        {"surface": "feed", "ad_formats": []},
        {"surface": "feed", "ad_formats": ["feed", "feed"]},
        {"surface": "feed", "ad_formats": ["newsletter"]},
        {"surface": "feed", "ad_formats": ["header_sponsorship"]},
        {"surface": "topic", "topic_id": str(uuid.uuid4()), "ad_formats": ["sponsored_content"]},
        {
            "surface": "article",
            "article_id": str(uuid.uuid4()),
            "ad_formats": ["header_sponsorship"],
        },
    ],
)
def test_request_formats_reject_ambiguous_or_unsupported_surfaces(payload):
    with pytest.raises(ValidationError):
        PlacementQuery(**payload)


@pytest.mark.parametrize(
    "kind,surfaces", [("header_sponsorship", ["header"]), ("sponsored_content", ["feed"])]
)
def test_fixed_location_campaigns_have_safe_surface_defaults(kind, surfaces):
    values = dict(
        name="Native agreement",
        ad_format=kind,
        starts_at=utcnow(),
        ends_at=utcnow() + timedelta(days=1),
    )
    if kind == "sponsored_content":
        values["article_id"] = uuid.uuid4()
    assert CampaignInput(**values).surfaces == surfaces
    with pytest.raises(ValidationError):
        CampaignInput(**values, surfaces=["article"])


def native_account(database, catalog, formats):
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.tier = "silver"
        account.promotion_kind = None
        account.ad_formats = formats
        # Keep the old explicit agreement from shadowing the standing feed policy.
        session.get(PartnerCampaign, catalog["campaign"]).status = "paused"
        ensure_automatic_assets(session, account.id)


def feed_request(catalog, formats, *, fallback=False):
    return PlacementQuery(
        surface="feed",
        article_ids=[catalog["article"]],
        ad_formats=formats,
        allow_sponsored_fallback=fallback,
    )


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["feed", "header_sponsorship", "engagement"])
def test_automatic_native_formats_deliver_owned_relevant_inventory(database, feed_catalog, kind):
    native_account(database, feed_catalog, [kind])
    payload = (
        PlacementQuery(surface="header", ad_formats=[kind], article_ids=[feed_catalog["article"]])
        if kind == "header_sponsorship"
        else feed_request(feed_catalog, [kind])
    )
    rows = deliver(database, payload)
    assert len(rows) == (2 if kind == "feed" else 1)
    rows = [row for row in rows if row["sponsored"]]
    assert rows[0]["product_id"] == feed_catalog["paid_product"]
    assert rows[0]["ad_format"] == kind
    assert rows[0]["sponsored"] is True
    assert rows[0]["article"] is None
    with database() as session:
        reservation = session.scalar(select(PartnerPlacementDelivery))
        assert reservation.surface == ("header" if kind == "header_sponsorship" else "feed")


@pytest.mark.integration
def test_engagement_never_uses_unrelated_fallback_and_total_formats_are_bounded(
    database, feed_catalog
):
    native_account(database, feed_catalog, ["feed", "header_sponsorship", "engagement"])
    empty = PlacementQuery(surface="feed", ad_formats=["engagement"], allow_sponsored_fallback=True)
    assert deliver(database, empty) == []
    rows = deliver(
        database,
        feed_request(feed_catalog, ["feed", "engagement"], fallback=True),
    )
    assert len(rows) == 2
    assert len({row["product_id"] for row in rows}) == 2
    assert len({row["ad_format"] for row in rows}) == 2
    assert [row["ad_format"] for row in rows] == ["feed", "engagement"]
    assert [row["sponsored"] for row in rows] == [False, True]


@pytest.mark.integration
def test_each_automatic_format_respects_live_disable_and_tier_downgrade(database, feed_catalog):
    native_account(database, feed_catalog, ["engagement"])
    payload = feed_request(feed_catalog, ["engagement"])
    receipt = deliver(database, payload)[0]["receipt"]
    with database.begin() as session:
        session.get(PartnerAccount, feed_catalog["paid"]).tier = "bronze"
    assert deliver(database, payload) == []
    with database() as session, pytest.raises(InactivePartnerAsset):
        record_delivery_event(session, PartnerTrackingTokens(KEY).verify(receipt), "impression")
    with database.begin() as session:
        account = session.get(PartnerAccount, feed_catalog["paid"])
        account.tier = "silver"
        account.ad_formats = []
    assert deliver(database, payload) == []


def sponsored_article(database, catalog):
    with database.begin() as session:
        article = Article(
            canonical_url="https://publisher.example/paid-story",
            url_hash=uuid.uuid4().hex,
            title="Kubernetes operations explained",
            summary="Practical Kubernetes operations.",
            content_type="article",
            review_status="approved",
            publication_status="published",
        )
        session.add(article)
        session.flush()
        session.add(
            ArticleTopic(
                article_id=article.id,
                topic_id=catalog["topic"],
                role="primary",
                relevance=1.0,
                evidence="Kubernetes is the primary subject.",
            )
        )
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=catalog["source"],
                entry_key="paid-story",
                original_url=article.canonical_url,
            )
        )
        return article.id


def campaign_payload(article_id, **overrides):
    return dict(
        name="Sponsored operations article",
        status="active",
        ad_format="sponsored_content",
        article_id=str(article_id),
        targeting="all",
        starts_at=(utcnow() - timedelta(minutes=1)).isoformat(),
        ends_at=(utcnow() + timedelta(days=1)).isoformat(),
        **overrides,
    )


@pytest.mark.integration
def test_sponsored_content_is_real_visible_article_with_permanent_disclosure_and_ledger(
    database, feed_catalog, admin_portal_client, monkeypatch
):
    from unittest.mock import Mock

    from devfeed_core import cache_events

    invalidated = Mock()
    monkeypatch.setattr(cache_events, "invalidate_public_cache", invalidated)
    native_account(database, feed_catalog, [])
    article_id = sponsored_article(database, feed_catalog)
    client, _ = admin_portal_client
    prefix = f"/v1/admin/partner-accounts/{feed_catalog['paid']}"
    invalidated.reset_mock()
    created = client.post(prefix + "/campaigns", json=campaign_payload(article_id))
    assert created.status_code == 201, created.text
    invalidated.assert_called()
    campaign_id = created.json()["id"]
    rows = deliver(database, feed_request(feed_catalog, ["sponsored_content"]))
    assert len(rows) == 1
    row = rows[0]
    assert row["article"].id == article_id
    assert row["article"].sponsor_names == ["Paid partner"]
    assert row["url"] == f"/articles/{row['article'].slug}"
    assert row["campaign_id"] == uuid.UUID(campaign_id)
    with database.begin() as session:
        session.get(PartnerCampaign, uuid.UUID(campaign_id)).status = "ended"
    assert deliver(database, feed_request(feed_catalog, ["sponsored_content"])) == []
    with database() as session:
        assert session.get(Article, article_id).sponsor_names == ["Paid partner"]
        assert session.get(Article, article_id).publication_status == "published"
        assert session.get(PartnerAsset, row["asset_id"]).article_id == article_id


@pytest.mark.integration
def test_sponsored_creative_change_retires_old_receipt_without_rewriting_asset(
    database, feed_catalog, admin_portal_client
):
    native_account(database, feed_catalog, [])
    first = sponsored_article(database, feed_catalog)
    client, _ = admin_portal_client
    prefix = f"/v1/admin/partner-accounts/{feed_catalog['paid']}/campaigns"
    created = client.post(prefix, json=campaign_payload(first))
    assert created.status_code == 201, created.text
    row = deliver(database, feed_request(feed_catalog, ["sponsored_content"]))[0]
    with database.begin() as session:
        other = session.get(Article, feed_catalog["article"])
        other.title = "Updated sponsored creative"
    second = feed_catalog["article"]
    updated = client.put(prefix + "/" + created.json()["id"], json=campaign_payload(second))
    assert updated.status_code == 200, updated.text
    with database() as session, pytest.raises(InactivePartnerAsset):
        record_delivery_event(
            session, PartnerTrackingTokens(KEY).verify(row["receipt"]), "impression"
        )
    with database() as session:
        assert session.get(PartnerAsset, row["asset_id"]).article_id == first
        assert session.get(Article, first).sponsor_names == ["Paid partner"]
        assert session.get(Article, second).sponsor_names == ["Paid partner"]
    # This article is already visible in the feed, so it cannot be injected again.
    assert deliver(database, feed_request(feed_catalog, ["sponsored_content"])) == []


@pytest.mark.integration
def test_sponsor_catalog_and_creative_preview_return_current_safe_records(
    database, feed_catalog, admin_portal_client
):
    client, _ = admin_portal_client
    prefix = f"/v1/admin/partner-accounts/{feed_catalog['paid']}"
    catalog = client.get(
        prefix + "/sponsor-articles", params={"article_id": str(feed_catalog["article"])}
    )
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["total"] == 1
    assert catalog.json()["items"][0]["title"] == "Monitor Kubernetes clusters"
    preview = client.get(
        prefix + "/creative-preview", params={"product_id": str(feed_catalog["paid_product"])}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["product"]["id"] == str(feed_catalog["paid_product"])
    assert preview.json()["product"]["logo_url"] is None
    assert preview.json()["product"]["image_url"] is None
    assert "utm_source=devfeed" in preview.json()["product"]["product_url"]
    foreign = client.get(
        prefix + "/creative-preview", params={"product_id": str(feed_catalog["free_product"])}
    )
    assert foreign.json() == {"product": None}


@pytest.mark.integration
def test_format_reporting_filters_immutable_asset_rows(database, feed_catalog):
    native_account(database, feed_catalog, ["feed", "engagement"])
    with database() as session:
        account = session.get(PartnerAccount, feed_catalog["paid"])
        report = reporting_dashboard(
            account.id, session, account, 30, 100, 0, ad_format="engagement"
        )
        assert report["assets"]
        assert all(row["ad_format"] == "engagement" for row in report["assets"])
        assert report["asset_total"] == len(report["assets"])


@pytest.mark.integration
@pytest.mark.parametrize(
    "existing,incoming,expected",
    [
        (None, ["engagement"], ["engagement"]),
        (["feed"], None, ["feed"]),
        (["feed", "engagement"], ["engagement"], ["engagement"]),
        (["feed"], ["engagement"], []),
        ([], ["feed"], []),
    ],
)
def test_identity_merge_conservatively_preserves_product_allowlists(
    database, catalog, existing, incoming, expected
):
    from devfeed_core.models import PartnerProductPromotion
    from devfeed_core.partner_catalog import merge_products

    with database.begin() as session:
        recipient = session.get(PartnerProduct, catalog["paid_product"])
        donor = session.get(PartnerProduct, catalog["unrelated"])
        session.add_all(
            [
                PartnerProductPromotion(
                    account_id=catalog["paid"],
                    product_id=recipient.id,
                    ad_formats=existing,
                    promotion_surfaces=["feed", "article"],
                ),
                PartnerProductPromotion(
                    account_id=catalog["paid"],
                    product_id=donor.id,
                    ad_formats=incoming,
                    promotion_surfaces=["feed"],
                    excluded=True,
                ),
            ]
        )
        session.flush()
        merge_products(session, donor, recipient)
        policy = session.get(PartnerProductPromotion, (catalog["paid"], recipient.id))
        assert policy.ad_formats == expected
        assert policy.promotion_surfaces == ["feed"]
        assert policy.excluded
        assert session.get(PartnerProductPromotion, (catalog["paid"], donor.id)) is not None


@pytest.mark.integration
def test_concurrent_campaign_activation_preserves_each_permanent_sponsor(database, catalog):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from devfeed_http.partner_campaigns import CampaignService

    article_id = sponsored_article(database, catalog)
    barrier = Barrier(2)

    def activate(account_id):
        with database.begin() as session:
            account = session.get(PartnerAccount, account_id)
            payload = CampaignInput(**campaign_payload(article_id))
            barrier.wait(timeout=10)
            CampaignService(session, account).record_sponsorship(payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(activate, [catalog["free"], catalog["paid"]]))
    with database() as session:
        assert session.get(Article, article_id).sponsor_names == ["Free partner", "Paid partner"]


@pytest.mark.integration
def test_relevant_product_cannot_promote_unrelated_or_untagged_article(
    database, feed_catalog, admin_portal_client
):
    native_account(database, feed_catalog, [])
    article_id = sponsored_article(database, feed_catalog)
    client, _ = admin_portal_client
    prefix = f"/v1/admin/partner-accounts/{feed_catalog['paid']}"
    created = client.post(prefix + "/campaigns", json=campaign_payload(article_id))
    assert created.status_code == 201, created.text
    payload = feed_request(feed_catalog, ["sponsored_content"])
    assert len(deliver(database, payload)) == 1
    with database.begin() as session:
        link = session.get(ArticleTopic, (article_id, feed_catalog["topic"]))
        session.delete(link)
        session.add(
            ArticleTopic(
                article_id=article_id,
                topic_id=feed_catalog["other_topic"],
                role="primary",
                relevance=1.0,
                evidence="Figma is now the primary subject.",
            )
        )
    assert deliver(database, payload) == []
    with database.begin() as session:
        session.delete(session.get(ArticleTopic, (article_id, feed_catalog["other_topic"])))
    assert deliver(database, payload) == []
    listed = client.get(prefix + "/sponsor-articles", params={"article_id": str(article_id)})
    assert listed.json()["total"] == 0
    rejected = client.put(
        prefix + "/campaigns/" + created.json()["id"], json=campaign_payload(article_id)
    )
    assert rejected.status_code == 422, rejected.text
    with database() as session:
        assert session.get(Article, article_id).sponsor_names == ["Paid partner"]


@pytest.mark.integration
@pytest.mark.parametrize("reference", ["campaign", "historical_asset"])
def test_unpublished_sponsored_article_delete_preserves_history_with_clear_conflict(
    database, feed_catalog, admin_client, reference
):
    article_id = sponsored_article(database, feed_catalog)
    with database.begin() as session:
        account = session.get(PartnerAccount, feed_catalog["paid"])
        campaign = PartnerCampaign(
            account_id=account.id,
            name="Preserved sponsorship",
            status="draft" if reference == "campaign" else "active",
            ad_format="sponsored_content",
            article_id=article_id,
            surfaces=["feed"],
            starts_at=utcnow() - timedelta(days=1),
            ends_at=utcnow() + timedelta(days=1),
        )
        session.add(campaign)
        session.flush()
        if reference == "historical_asset":
            ensure_automatic_assets(session, account.id)
            assert session.scalar(
                select(PartnerAsset.id).where(PartnerAsset.article_id == article_id)
            )
            # Current creative no longer refers to this story, but its immutable
            # historical delivery asset still does.
            campaign.article_id = feed_catalog["article"]
        session.get(Article, article_id).publication_status = "unpublished"
    response = admin_client.delete(f"/v1/admin/articles/{article_id}")
    assert response.status_code == 409, response.text
    assert "sponsorship history" in response.text
    with database() as session:
        assert session.get(Article, article_id).publication_status == "unpublished"
