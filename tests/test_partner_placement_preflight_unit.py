"""Audience preflight preserves policy while bounding reads for each candidate batch."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import partner_placement_preflight as preflight
from devfeed_core.models import PartnerAccount, PartnerAsset, PartnerCampaign, PartnerProduct
from devfeed_core.partner_audiences import ReaderAudience
from devfeed_http.partner_placements import PlacementQuery

NOW = datetime(2026, 10, 12, tzinfo=UTC)
LANGUAGE, SECOND_LANGUAGE, TOOL, SECOND_TOOL = (uuid.uuid4() for _ in range(4))
USER = uuid.uuid4()
QUERY = PlacementQuery(surface="article", article_id=uuid.uuid4())


def candidate(audience, *, override=None, tier="platinum"):
    account = PartnerAccount(
        id=uuid.uuid4(),
        name="Audience partner",
        status="active",
        tier=tier,
        promotion_kind="ad",
        organic_enabled=True,
        auto_include_products=True,
        promotion_surfaces=["article", "topic", "feed"],
        ad_formats=["feed"],
        audience=audience,
    )
    product = PartnerProduct(
        id=uuid.uuid4(),
        name="Verified Python tool",
        product_url="https://tool.example/",
        technologies=["Python"],
        revision=1,
        updated_at=NOW,
    )
    campaign = PartnerCampaign(
        id=uuid.uuid4(),
        account_id=account.id,
        name="Audience agreement",
        status="active",
        targeting="all",
        managed=False,
        product_ids=[],
        technologies=[],
        topic_ids=[],
        surfaces=["article", "topic", "feed"],
        ad_format="feed",
        article_id=None,
        audience=override,
        starts_at=NOW - timedelta(days=1),
        ends_at=NOW + timedelta(days=1),
    )
    asset = PartnerAsset(
        id=uuid.uuid4(),
        account_id=account.id,
        campaign_id=campaign.id,
        product_id=product.id,
        status="active",
        automatic=True,
        ad_format="feed",
        article_id=None,
    )
    return asset, account, product, campaign


def selected_products(rows):
    return [product.id for _asset, _account, product, _campaign in rows]


@pytest.mark.parametrize(
    "audience,tier,user_id,reader,reader_reads",
    [
        ({"experience_levels": ["senior"]}, "platinum", None, None, 0),
        ({"experience_levels": ["lead"]}, "platinum", USER, ReaderAudience("senior"), 0),
        ({"experience_levels": ["senior"]}, "gold", USER, ReaderAudience("senior"), 0),
        ({"experience_levels": ["senior"]}, "platinum", USER, ReaderAudience("entry_level"), 1),
    ],
    ids=["anonymous", "invalid-policy", "downgraded-account", "mismatched-reader"],
)
def test_obvious_audience_rejections_need_no_candidate_policy_reads(
    monkeypatch, audience, tier, user_id, reader, reader_reads
):
    session = Mock()
    reader_lookup = Mock(return_value=reader)
    monkeypatch.setattr(preflight, "reader_audience", reader_lookup)
    selector = preflight.PlacementPreflight(session, QUERY, NOW, user_id)
    rows = [candidate(audience, tier=tier) for _ in range(105)]

    assert selected_products(selector.eligible_rows(rows)) == []
    session.execute.assert_not_called()
    assert reader_lookup.call_count == reader_reads


def test_explicit_empty_campaign_audience_clears_inherited_targeting_without_private_reads(
    monkeypatch,
):
    session = Mock()
    reader_lookup = Mock(side_effect=AssertionError("Untargeted inventory needs no reader profile"))
    monkeypatch.setattr(preflight, "reader_audience", reader_lookup)
    row = candidate({"experience_levels": ["senior"]}, override={}, tier="gold")
    selector = preflight.PlacementPreflight(session, QUERY, NOW, None)

    assert selected_products(selector.eligible_rows([row])) == [row[2].id]
    session.execute.assert_not_called()
    reader_lookup.assert_not_called()


def test_signed_in_dimensions_share_one_topic_read_per_batch_and_one_reader_snapshot(monkeypatch):
    session = Mock()
    session.execute.return_value.all.return_value = [
        (LANGUAGE, "language"),
        (SECOND_LANGUAGE, "language"),
        (TOOL, "framework"),
        (SECOND_TOOL, "database"),
    ]
    reader_lookup = Mock(return_value=ReaderAudience("senior", {LANGUAGE, TOOL}))
    monkeypatch.setattr(preflight, "reader_audience", reader_lookup)
    selector = preflight.PlacementPreflight(session, QUERY, NOW, USER)
    policies = [
        {
            "experience_levels": ["senior", "mid_level"],
            "language_topic_ids": [str(LANGUAGE), str(SECOND_LANGUAGE)],
            "tool_topic_ids": [str(TOOL)],
        },
        {"experience_levels": ["entry_level"], "language_topic_ids": [str(LANGUAGE)]},
        {"experience_levels": ["senior"], "language_topic_ids": [str(SECOND_LANGUAGE)]},
        {"experience_levels": ["senior"], "tool_topic_ids": [str(SECOND_TOOL)]},
        {"experience_levels": ["senior"]},
    ]
    for batch_number in range(2):
        rows = [candidate(policy) for _ in range(20) for policy in policies]
        expected = [row[2].id for index, row in enumerate(rows) if index % len(policies) in {0, 4}]

        assert selected_products(selector.eligible_rows(rows)) == expected
        assert session.execute.call_count == batch_number + 1
    assert reader_lookup.call_count == 1


@pytest.mark.parametrize(
    "canonical_topics,eligible",
    [
        ([(LANGUAGE, "language"), (TOOL, "framework")], True),
        ([(LANGUAGE, "framework"), (TOOL, "framework")], False),
        ([(LANGUAGE, "language"), (TOOL, "language")], False),
        ([(TOOL, "framework")], False),
    ],
    ids=["current", "wrong-language-kind", "wrong-tool-kind", "missing-or-retired-language"],
)
def test_matching_reader_cannot_bypass_current_canonical_audience_topics(
    monkeypatch, canonical_topics, eligible
):
    session = Mock()
    # A topic retired after the reader snapshot is absent from current active
    # taxonomy, while a changed kind is present with its current kind.
    session.execute.return_value.all.return_value = canonical_topics
    monkeypatch.setattr(
        preflight, "reader_audience", lambda *_: ReaderAudience("senior", {LANGUAGE, TOOL})
    )
    row = candidate(
        {
            "experience_levels": ["senior"],
            "language_topic_ids": [str(LANGUAGE)],
            "tool_topic_ids": [str(TOOL)],
        }
    )
    selector = preflight.PlacementPreflight(session, QUERY, NOW, USER)

    assert selected_products(selector.eligible_rows([row])) == ([row[2].id] if eligible else [])
    assert session.execute.call_count == 1
