"""Campaign commands enforce entitlements and preserve explicit targeting policy.

Session results are supplied repository reads, including already aggregated metrics.
These cases do not evaluate SQL ownership filters, locks, or transaction atomicity.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import PartnerAccount, PartnerCampaign, PartnerProduct
from devfeed_http import partner_campaigns, partner_portal
from devfeed_http.partner_campaigns import CampaignService
from devfeed_http.partner_portal import CampaignInput
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from tests.articles.test_article_lifecycle import facts as completed_article_facts
from tests.support.partner_matching import NOW as PARTNER_NOW

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def agreement(**changes):
    return CampaignInput(
        **{
            "name": "Autumn developer campaign",
            "starts_at": NOW,
            "ends_at": NOW + timedelta(days=30),
            **changes,
        }
    )


def stored_campaign(account, **changes):
    return PartnerCampaign(
        id=uuid.uuid4(),
        account_id=account.id,
        managed=False,
        **agreement(**changes).model_dump(),
    )


@pytest.fixture
def campaigns(monkeypatch):
    account = PartnerAccount(
        id=uuid.uuid4(), name="Dev tooling partner", tier="platinum", status="active"
    )
    session = Mock(spec=Session)
    enrollment = Mock()
    monkeypatch.setattr(partner_campaigns, "request_promotion_enrollment", enrollment)
    return CampaignService(session, account), session, account, enrollment


def test_new_campaign_preserves_explicit_selections_and_defers_transaction_ownership(campaigns):
    service, session, account, enrollment = campaigns
    product = PartnerProduct(id=uuid.uuid4(), name="Developer toolkit")
    # An already resolved account-catalog read; the SQL association is integration tested.
    session.execute.return_value = [(object(), product)]
    payload = agreement(
        targeting="selected",
        product_ids=[product.id],
        audience={"experience_levels": ["senior"]},
        status="active",
        priority=80,
        daily_impression_limit=1200,
    )

    saved = service.save(payload)

    assert isinstance(saved, PartnerCampaign) and saved.account_id == account.id
    assert saved.product_ids == [str(product.id)]
    assert saved.topic_ids == [] and saved.technologies == []
    assert saved.audience == {
        "experience_levels": ["senior"],
        "language_topic_ids": [],
        "tool_topic_ids": [],
    }
    assert (saved.status, saved.priority, saved.daily_impression_limit) == ("active", 80, 1200)
    session.add.assert_called_once_with(saved)
    session.flush.assert_called_once()
    enrollment.assert_called_once_with(session, account.id)
    session.commit.assert_not_called()


@pytest.mark.parametrize("audience", [None, {}])
def test_campaign_audience_distinguishes_inheritance_from_explicit_all_readers(campaigns, audience):
    service, session, account, enrollment = campaigns
    account.tier = "silver"
    current = stored_campaign(account, audience={"experience_levels": ["senior"]})
    session.scalar.return_value = current

    saved = service.save(agreement(audience=audience, status="paused"), campaign_id=current.id)

    assert saved is current and current.status == "paused"
    assert current.audience == (
        None
        if audience is None
        else {"experience_levels": [], "language_topic_ids": [], "tool_topic_ids": []}
    )
    session.add.assert_not_called()
    enrollment.assert_called_once_with(session, account.id)


def test_downgraded_account_can_retain_but_cannot_expand_its_saved_audience(campaigns):
    service, session, account, enrollment = campaigns
    account.tier = "silver"
    current = stored_campaign(account, audience={"experience_levels": ["senior"]})
    session.scalar.return_value = current
    saved = service.save(
        agreement(audience={"experience_levels": ["senior"]}, name="Renamed agreement"),
        campaign_id=current.id,
    )
    assert saved is current and current.name == "Renamed agreement"
    enrollment.reset_mock()
    session.flush.reset_mock()

    with pytest.raises(HTTPException) as denied:
        service.save(
            agreement(audience={"experience_levels": ["senior", "mid_level"]}),
            campaign_id=current.id,
        )

    assert denied.value.status_code == 422 and "Platinum or Diamond" in denied.value.detail
    assert current.audience["experience_levels"] == ["senior"]
    assert current.name == "Renamed agreement"
    enrollment.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("reason", ["missing", "managed", "unentitled"])
def test_unavailable_or_managed_campaigns_cannot_be_saved(campaigns, reason):
    service, session, account, enrollment = campaigns
    current = stored_campaign(account)
    session.scalar.return_value = None if reason == "missing" else current
    current.managed = reason == "managed"
    if reason == "unentitled":
        account.tier = "bronze"

    with pytest.raises(HTTPException) as denied:
        service.save(agreement(name="Unauthorized edit"), campaign_id=current.id)

    assert denied.value.status_code == {"missing": 404, "managed": 409, "unentitled": 422}[reason]
    assert current.name == "Autumn developer campaign"
    session.add.assert_not_called()
    session.flush.assert_not_called()
    enrollment.assert_not_called()


@pytest.mark.parametrize("scope", ["products", "topics"])
@pytest.mark.parametrize("complete", [False, True])
def test_targeting_requires_the_complete_requested_repository_selection(campaigns, scope, complete):
    service, session, _, enrollment = campaigns
    identifiers = [uuid.uuid4(), uuid.uuid4()]
    returned = identifiers if complete else identifiers[:1]
    if scope == "products":
        payload = agreement(targeting="selected", product_ids=identifiers)
        session.execute.return_value = [
            (object(), PartnerProduct(id=value, name=f"Tool {index}"))
            for index, value in enumerate(returned)
        ]
    else:
        payload = agreement(targeting="filters", topic_ids=identifiers, technologies=["Python"])
        session.scalars.return_value = returned

    if complete:
        saved = service.save(payload)
        assert getattr(saved, "product_ids" if scope == "products" else "topic_ids") == [
            str(value) for value in identifiers
        ]
        enrollment.assert_called_once()
    else:
        with pytest.raises(HTTPException) as denied:
            service.save(payload)
        assert denied.value.status_code == 422
        assert (
            "products must belong" in denied.value.detail
            if scope == "products"
            else ("topics must be active" in denied.value.detail)
        )
        session.add.assert_not_called()
        session.flush.assert_not_called()
        enrollment.assert_not_called()


def published_article():
    article, facts = completed_article_facts()
    origin = article.origins[0]
    origin.source.feed_url = "https://publisher.example/feed"
    origin.source.enabled = True
    origin.entry_key = str(article.id)
    origin.original_url = article.canonical_url
    for target in ("enrichment", "analysis"):
        job = facts[target]
        job.created_at = job.available_at = facts["now"] - timedelta(minutes=3)
        job.finished_at = facts["now"] - timedelta(minutes=1)
        job.lease_token = job.lease_until = None
    # All processing is complete before this read projection becomes published.
    assert derive_publication_readiness(article, **facts).stage == "ready"
    article.publication_status = "published"
    article.published_to_feed_at = facts["now"]
    article.sponsor_names = ["Existing sponsor"]
    assert derive_publication_readiness(article, **facts).stage == "published"
    return article


@pytest.mark.parametrize("missing", ["article", "topics"])
def test_sponsored_content_requires_both_a_visible_article_and_active_topics(campaigns, missing):
    service, session, _, enrollment = campaigns
    article = published_article()
    session.scalar.side_effect = [None] if missing == "article" else [article, None]

    with pytest.raises(HTTPException) as denied:
        service.save(agreement(ad_format="sponsored_content", article_id=article.id))

    assert denied.value.status_code == 422
    assert (
        "visible article" if missing == "article" else "active article topics"
    ) in denied.value.detail
    assert article.sponsor_names == ["Existing sponsor"]
    session.add.assert_not_called()
    enrollment.assert_not_called()


@pytest.mark.parametrize("status", ["draft", "active", "paused", "ended"])
@pytest.mark.parametrize("already_disclosed", [False, True])
def test_only_activation_adds_a_permanent_deduplicated_article_disclosure(
    campaigns, status, already_disclosed
):
    service, session, account, _ = campaigns
    article = published_article()
    article.sponsor_names = ["Existing sponsor"] + ([account.name] if already_disclosed else [])
    prior_disclosure = list(article.sponsor_names)
    before = (article.title, article.summary, article.review_status, article.publication_status)
    session.scalar.side_effect = [article, article.id]

    saved = service.save(
        agreement(ad_format="sponsored_content", article_id=article.id, status=status)
    )

    assert saved.article_id == article.id and saved.surfaces == ["feed"]
    assert article.sponsor_names == (
        sorted({"Existing sponsor", account.name}) if status == "active" else prior_disclosure
    )
    assert (
        article.title,
        article.summary,
        article.review_status,
        article.publication_status,
    ) == before


def test_campaign_reporting_preserves_order_and_handles_zero_or_absent_measurements(campaigns):
    service, session, account, _ = campaigns
    measured = stored_campaign(account, name="Measured")
    zero = stored_campaign(account, name="Zero impressions")
    absent = stored_campaign(account, name="No measurements")
    first, second = uuid.uuid4(), uuid.uuid4()
    session.execute.return_value.all.return_value = [(first, measured.id), (second, zero.id)]
    analytics = Mock()
    analytics.partner_summary = AsyncMock(
        return_value=[
            {"owner": str(first), "impressions": 400, "clicks": 10},
            {"owner": str(second), "impressions": 0, "clicks": 0},
        ]
    )
    reports = asyncio.run(service.reports([zero, measured, absent], analytics))
    assert [(row.name, row.impressions, row.clicks, row.ctr) for row in reports] == [
        ("Zero impressions", 0, 0, None),
        ("Measured", 400, 10, 2.5),
        ("No measurements", 0, 0, None),
    ]
    assert [row.id for row in reports] == [zero.id, measured.id, absent.id]
    assert measured.name == "Measured" and measured.account_id == account.id
    session.commit.assert_not_called()


def test_empty_campaign_reporting_performs_no_metric_read(campaigns):
    service, session, _, _ = campaigns
    analytics = Mock()
    analytics.partner_summary = AsyncMock()
    assert asyncio.run(service.reports([], analytics)) == []
    session.execute.assert_not_called()
    analytics.partner_summary.assert_not_called()


def test_campaign_get_returns_reported_account_record_or_not_found(campaigns):
    service, session, account, _ = campaigns
    campaign = stored_campaign(account)
    session.scalar.return_value = campaign
    asset = uuid.uuid4()
    session.execute.return_value.all.return_value = [(asset, campaign.id)]
    analytics = Mock()
    analytics.partner_summary = AsyncMock(
        return_value=[{"owner": str(asset), "impressions": 20, "clicks": 1}]
    )
    report = asyncio.run(service.get(campaign.id, analytics))
    assert report.id == campaign.id and report.account_id == account.id and report.ctr == 5.0
    session.scalar.return_value = None
    with pytest.raises(HTTPException) as missing:
        asyncio.run(service.get(uuid.uuid4(), analytics))
    assert missing.value.status_code == 404


@pytest.mark.parametrize("ad_format,article_id", [("feed", True), ("sponsored_content", False)])
def test_campaign_article_identity_is_required_only_for_sponsored_content(ad_format, article_id):
    with pytest.raises(ValidationError, match="Supply an article only for sponsored content"):
        partner_portal.CampaignInput(
            name="Developer campaign",
            ad_format=ad_format,
            article_id=uuid.uuid4() if article_id else None,
            starts_at=PARTNER_NOW,
            ends_at=PARTNER_NOW + timedelta(days=1),
        )
