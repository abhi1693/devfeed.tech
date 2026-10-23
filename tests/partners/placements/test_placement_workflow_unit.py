"""Placement issuance decisions over ranked repository rows and live policy services.

Savepoint calls below are requested unit-of-work actions, not proofs of database
rollback/locking. Actual eligibility predicates, schemas and receipt crypto are used
in their own policy cases; ranked selection and external reads are supplied inputs.
"""

import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_placements as placements
from devfeed_core import partner_tools
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerProduct,
    PartnerProductTopic,
)
from devfeed_core.partner_placement_destinations import AccountProductCreative
from devfeed_core.partner_tracking import BusyPartnerAsset, PartnerTrackingTokens
from devfeed_http.partner_placements import PlacementOut, PlacementQuery
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.support.partner_accounts import (
    account as account,
)
from tests.support.partner_accounts import (
    asset as asset,
)
from tests.support.partner_accounts import (
    campaign as campaign,
)
from tests.support.partner_matching import NOW, article, product

KEY = "disposable-partner-workflow-signing-secret"


@pytest.fixture
def delivery(monkeypatch):
    owner, value = account(), product()
    agreement = campaign(owner)
    creative = asset(owner, value, agreement)
    session = Mock(spec=Session)
    session.scalar.side_effect = [True, "Current grounded publisher task"]
    session.get.return_value = None
    matches = select(
        PartnerProductTopic.product_id,
        PartnerProductTopic.score,
        PartnerProductTopic.reason,
        PartnerProductTopic.product_revision,
        PartnerProductTopic.product_updated_at,
    )
    monkeypatch.setattr(placements, "contextual_candidates", lambda *_, **__: matches)
    preflight = Mock()
    preflight.campaign_exhausted.return_value = False
    preflight.destinations.unavailable.return_value = False
    monkeypatch.setattr(placements, "PlacementPreflight", lambda *_: preflight)
    locks, available, capacity = (
        Mock(return_value=True),
        Mock(return_value=True),
        Mock(return_value=True),
    )
    monkeypatch.setattr(placements, "lock_attribution_row", locks)
    monkeypatch.setattr(placements, "automatic_asset_available", available)
    monkeypatch.setattr(placements, "campaign_capacity", capacity)
    destination = Mock(return_value=AccountProductCreative(value.tagline, None))
    monkeypatch.setattr(placements, "account_product_creative", destination)
    state = NS(
        owner=owner,
        product=value,
        campaign=agreement,
        asset=creative,
        session=session,
        preflight=preflight,
        locks=locks,
        available=available,
        capacity=capacity,
        destination=destination,
        phase="sponsored",
        revision=value.revision,
        story_matches=True,
        rows=None,
    )

    def ranked(*_, phase, **__):
        if phase != state.phase:
            return
        if state.rows is not None:
            yield from state.rows
        else:
            yield (
                state.asset,
                state.owner,
                state.product,
                state.campaign,
                90,
                "Ranked reason",
                state.revision,
                state.product.updated_at,
                state.story_matches,
            )

    monkeypatch.setattr(placements, "ranked_placement_rows", ranked)
    return state


def issue(value, **changes):
    return placements.deliver_placements(
        value.session, PlacementQuery(surface="feed", ad_formats=["feed"], **changes), KEY, now=NOW
    )


def test_receipt_is_bound_to_saved_delivery_and_uses_final_context_reason(delivery):
    value = delivery
    [row] = issue(value)
    validated = PlacementOut.model_validate(row)
    saved = value.session.add.call_args.args[0]
    claims = PartnerTrackingTokens(KEY, clock=lambda: NOW.timestamp()).verify(validated.receipt)
    assert claims.asset_id == value.asset.id and claims.delivery_id == saved.delivery_id
    assert saved.asset_id == value.asset.id and saved.surface == "feed" and saved.issued_at == NOW
    assert validated.reason == "Current grounded publisher task" and validated.sponsored
    assert validated.url.startswith(value.product.product_url)
    assert validated.link_rel == ["nofollow"]
    value.session.begin_nested.return_value.commit.assert_called_once()


@pytest.mark.parametrize(
    "case",
    [
        "wrong_format",
        "already_shown_story",
        "unrelated_story",
        "wrong_phase",
        "campaign_exhausted",
        "destination_exhausted",
        "account_busy",
        "asset_busy",
        "product_busy",
        "campaign_changed",
        "revision_changed",
        "taxonomy_busy",
        "match_removed",
        "ownership_removed",
        "targeted_anonymous_reader",
        "capacity",
        "no_creative",
        "no_destination",
    ],
)
def test_final_policy_rejections_issue_no_delivery_and_preserve_next_candidate(delivery, case):
    value = delivery
    changes = {}
    if case == "wrong_format":
        value.asset.ad_format = "engagement"
    elif case in {"already_shown_story", "unrelated_story"}:
        story = article()
        value.asset.ad_format = value.campaign.ad_format = "sponsored_content"
        value.campaign.surfaces = ["feed"]
        value.asset.article_id = value.campaign.article_id = story.id
        value.story_matches = case != "unrelated_story"
        changes["article_ids"] = [story.id] if case == "already_shown_story" else []
    elif case == "wrong_phase":
        value.phase = "organic"
    elif case == "campaign_exhausted":
        value.preflight.campaign_exhausted.return_value = True
    elif case == "destination_exhausted":
        value.preflight.destinations.unavailable.return_value = True
    elif case.endswith("busy") and case != "taxonomy_busy":
        blocked = {
            "account_busy": PartnerAccount,
            "asset_busy": PartnerAsset,
            "product_busy": PartnerProduct,
        }[case]
        value.locks.side_effect = lambda _session, model, _key: model is not blocked
    elif case == "campaign_changed":
        value.asset.campaign_id = (
            uuid.uuid4()
        )  # Current asset no longer belongs to the ranked agreement.
    elif case == "revision_changed":
        value.revision -= 1
    elif case == "taxonomy_busy":
        value.session.scalar.side_effect = [False]
    elif case == "match_removed":
        value.session.scalar.side_effect = [True, None]
    elif case == "ownership_removed":
        value.available.return_value = False
    elif case == "targeted_anonymous_reader":
        value.campaign.audience = {"experience_levels": ["senior"]}
    elif case == "capacity":
        value.capacity.return_value = False
    elif case == "no_creative":
        value.destination.return_value = None
    elif case == "no_destination":
        value.owner.outbound_destination = "listing_url"
    formats = (
        ["sponsored_content"] if case in {"already_shown_story", "unrelated_story"} else ["feed"]
    )
    query = PlacementQuery(surface="feed", ad_formats=formats, **changes)
    assert placements.deliver_placements(value.session, query, KEY, now=NOW) == []
    value.session.add.assert_not_called()
    value.session.begin_nested.return_value.commit.assert_not_called()
    if case == "capacity":
        value.preflight.reject_campaign.assert_called_once_with(value.campaign)
    if case == "no_destination":
        value.preflight.destinations.reject.assert_called_once_with(
            value.owner.id, value.product.id
        )


def test_contextless_fallback_uses_disclosed_branding_reason(delivery, monkeypatch):
    value = delivery
    value.phase = "fallback"
    value.campaign.managed = True
    value.owner.promotion_kind = "ad"
    monkeypatch.setattr(placements, "contextual_candidates", lambda *_, **__: None)
    [row] = issue(value, allow_sponsored_fallback=True)
    assert row["reason"] == "Sponsored by this partner" and row["sponsored"]


def test_absent_signing_key_never_reads_candidates_or_creates_delivery():
    session = Mock(spec=Session)
    assert placements.deliver_placements(session, PlacementQuery(surface="feed"), "", now=NOW) == []
    assert session.mock_calls == []


@pytest.mark.parametrize(
    "surface,context,expected",
    [
        ("feed", False, {"feed"}),
        ("feed", True, set()),
        ("topic", False, set()),
        ("header", False, {"header_sponsorship"}),
    ],
)
def test_generic_fallback_never_broadens_visible_or_editorial_context(surface, context, expected):
    kwargs = {"topic_id": uuid.uuid4()} if surface == "topic" else {}
    if surface == "feed":
        kwargs["allow_sponsored_fallback"] = True
    query = PlacementQuery(
        surface=surface,
        ad_formats=["header_sponsorship"] if surface == "header" else ["feed"],
        **kwargs,
    )
    assert placements.fallback_formats(query, feed_context=context) == expected


def test_ranked_stream_preflights_each_batch_and_closes_repository_cursor(monkeypatch):
    owner, value = account(), product()
    agreement = campaign(owner)
    creative = asset(owner, value, agreement)
    row = creative, owner, value, agreement, 90, "Grounded task", value.revision, value.updated_at
    earlier_campaign = campaign(owner)
    earlier_asset = asset(owner, value, earlier_campaign)
    earlier_row = (earlier_asset, owner, value, earlier_campaign, *row[4:])
    session = Mock(spec=Session)
    cursor = session.execute.return_value
    cursor.partitions.return_value = [[earlier_row], [row]]
    preflight = Mock()
    preflight.eligible_rows.side_effect = [[], [row]]
    matches = select(
        PartnerProductTopic.product_id,
        PartnerProductTopic.score,
        PartnerProductTopic.reason,
        PartnerProductTopic.product_revision,
        PartnerProductTopic.product_updated_at,
    ).add_columns()
    matches = placements.placement_format_candidates(
        session, PlacementQuery(surface="feed", ad_formats=["feed"]), matches
    )
    stream = placements.ranked_placement_rows(
        session,
        PlacementQuery(surface="feed"),
        NOW,
        matches,
        phase="sponsored",
        seen=set(),
        selected_formats={"engagement"},
        preflight=preflight,
    )
    assert list(stream) == [(*row, True)]
    assert preflight.eligible_rows.call_count == 2
    cursor.close.assert_called_once()


@pytest.mark.parametrize(
    "kind", ["missing_context", "forbidden_fallback", "header_fallback", "feed_fallback"]
)
def test_ranked_stream_only_opens_repository_when_phase_has_eligible_context(kind):
    session = Mock(spec=Session)
    session.execute.return_value.partitions.return_value = []
    header = kind == "header_fallback"
    query = PlacementQuery(
        surface="header" if header else "feed",
        ad_formats=["header_sponsorship"] if header else ["feed"],
        allow_sponsored_fallback=kind == "feed_fallback",
    )
    assert (
        list(
            placements.ranked_placement_rows(
                session,
                query,
                NOW,
                None,
                phase="organic" if kind == "missing_context" else "fallback",
                seen=set(),
            )
        )
        == []
    )
    assert session.execute.call_count == (1 if kind in {"header_fallback", "feed_fallback"} else 0)


@pytest.mark.parametrize("busy_error", [False, True])
@pytest.mark.parametrize("exists", [False, True])
def test_sponsored_story_temporarily_unavailable_has_retryable_receipt_diagnostic(
    monkeypatch, busy_error, exists
):
    owner, value = account(), product()
    agreement = campaign(owner, ad_format="sponsored_content", article_id=uuid.uuid4())
    creative = asset(
        owner, value, agreement, ad_format="sponsored_content", article_id=agreement.article_id
    )
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    monkeypatch.setattr(placements, "account_product_available", lambda *_, **__: True)
    session = Mock(spec=Session)
    session.scalar.side_effect = [agreement, None, creative.article_id if exists else None]
    if busy_error and exists:
        with pytest.raises(BusyPartnerAsset, match="article is being updated"):
            placements.automatic_asset_available(
                session, creative, owner, value, NOW, surface="feed", busy_error=True
            )
    else:
        assert not placements.automatic_asset_available(
            session, creative, owner, value, NOW, surface="feed", busy_error=busy_error
        )


def test_standing_agreement_cannot_bypass_explicit_selected_inventory(monkeypatch):
    owner, value = account(promotion_kind="ad"), product()
    standing = campaign(owner, managed=True)
    explicit = campaign(owner, targeting="selected", product_ids=[str(value.id)])
    creative = asset(owner, value, standing)
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    monkeypatch.setattr(placements, "account_product_available", lambda *_, **__: True)
    session = Mock(spec=Session)
    session.scalar.return_value = standing
    session.scalars.return_value = [explicit]
    session.execute.return_value = []
    assert not placements.automatic_asset_available(
        session, creative, owner, value, NOW, surface="feed"
    )
