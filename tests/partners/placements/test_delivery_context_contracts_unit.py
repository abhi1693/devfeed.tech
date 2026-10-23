"""Preflight and final context witnesses preserve relevance without broadening scope.

Rows and count aggregates are scoped repository results. These units exercise
Python memoization and guard decisions, not database filter or lock correctness.
"""

import uuid
from unittest.mock import Mock

import pytest
from devfeed_core import partner_placement_preflight as preflight
from devfeed_core.partner_audiences import ReaderAudience
from devfeed_core.partner_search_context import PublicSearchContext
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy.orm import Session

from tests.support.partner_accounts import account, asset, campaign
from tests.support.partner_matching import NOW, product, result


def request(session, *, user_id=None):
    return preflight.PlacementPreflight(session, PlacementQuery(surface="feed"), NOW, user_id)


@pytest.mark.parametrize(
    "stop",
    [
        "account_paused",
        "asset_paused",
        "surface",
        "anonymous",
        "profile_mismatch",
        "missing_audience_topic",
        "capacity",
        "destination",
    ],
)
def test_preflight_rejects_ineligible_policy_before_authoritative_issuance(monkeypatch, stop):
    owner, value, session = account(), product(), Mock(spec=Session)
    agreement = campaign(owner)
    creative = asset(owner, value, agreement)
    if stop == "account_paused":
        owner.status = "paused"
    elif stop == "asset_paused":
        creative.status = "paused"
    elif stop == "surface":
        owner.promotion_surfaces = ["article"]
    elif stop in {"anonymous", "profile_mismatch"}:
        agreement.audience = {"experience_levels": ["senior"]}
    elif stop == "missing_audience_topic":
        agreement.audience = {"language_topic_ids": [str(uuid.uuid4())]}
    elif stop == "capacity":
        agreement.daily_impression_limit = 10
    user_id = uuid.uuid4() if stop in {"profile_mismatch", "missing_audience_topic"} else None
    reader = ReaderAudience(
        "entry_level",
        {uuid.UUID(identifier) for identifier in agreement.audience.get("language_topic_ids", [])},
    )
    monkeypatch.setattr(preflight, "reader_audience", lambda *_: reader)
    row = (creative, owner, value, agreement)
    check = request(session, user_id=user_id)
    session.execute.return_value = result(
        [] if stop == "missing_audience_topic" else [(agreement.id, 10)]
    )
    check.destinations = Mock()
    check.destinations.eligible_rows.side_effect = lambda rows: (
        [] if stop == "destination" else rows
    )
    assert check.eligible_rows([row]) == []
    assert check.campaign_exhausted(agreement) is (stop == "capacity")
    session.add.assert_not_called()


def test_capacity_rejection_is_memoized_and_does_not_repeat_usage_reads():
    owner, value, session = account(), product(), Mock(spec=Session)
    agreement = campaign(owner, daily_impression_limit=10)
    row = (asset(owner, value, agreement), owner, value, agreement)
    session.execute.return_value.all.return_value = [(agreement.id, 10)]
    check = request(session)
    assert check._rows_with_capacity([row]) == []
    assert check._rows_with_capacity([row]) == []
    session.execute.assert_called_once()


def test_private_reader_snapshot_is_loaded_once_and_shared_by_final_audience_checks(monkeypatch):
    reader, session, user_id = ReaderAudience("senior"), Mock(spec=Session), uuid.uuid4()
    load = Mock(return_value=reader)
    monkeypatch.setattr(preflight, "reader_audience", load)
    check = request(session, user_id=user_id)
    assert check.reader() is reader and check.reader() is reader
    load.assert_called_once_with(session, user_id, NOW)


@pytest.mark.parametrize(
    "case", ["explicit_selected", "explicit_other", "current_topic", "stale_topic"]
)
def test_managed_inventory_respects_explicit_campaign_ownership_and_current_product_topics(case):
    owner, value, session = account(promotion_kind="ad"), product(), Mock(spec=Session)
    topic_id = uuid.uuid4()
    standing = campaign(owner, managed=True)
    explicit = campaign(
        owner,
        targeting="selected",
        product_ids=[str(value.id)] if case == "explicit_selected" else [str(uuid.uuid4())],
    )
    if case.endswith("topic"):
        standing.targeting, standing.topic_ids = "filters", [str(topic_id)]
    row = (asset(owner, value, standing), owner, value, standing)
    check = request(session)
    check.destinations = Mock()
    check.destinations.eligible_rows.side_effect = lambda rows: rows
    prepared = [(value.id, topic_id, value.revision - int(case == "stale_topic"), value.updated_at)]
    session.execute.side_effect = [[explicit], prepared] if case.endswith("topic") else [[explicit]]
    output = check.eligible_rows([row])
    assert output == ([] if case in {"explicit_selected", "stale_topic"} else [row])


@pytest.mark.parametrize("context", ["article", "topic", "tag", "source"])
@pytest.mark.parametrize("available", [False, True])
def test_search_witness_requires_current_approved_source_and_stops_after_lost_context(
    context, available
):
    article_id, topic_id, tag_id, source_id, product_id = [uuid.uuid4() for _ in range(5)]
    results = (
        {"articles": {article_id}}
        if context == "article"
        else {"topics": {topic_id}}
        if context == "topic"
        else {"tags": {tag_id}}
        if context == "tag"
        else {"sources": {source_id}}
    )
    witness = PublicSearchContext(results)
    session = Mock(spec=Session)
    # The scoped repository read already links this article/topic witness to
    # product_id through its current prepared product-topic edge. The read's
    # projection contains only Article.id and Topic.id, not the product identity.
    session.execute.return_value.first.return_value = (article_id, topic_id)
    session.scalar.side_effect = (
        [tag_id, source_id if available else None]
        if context == "tag"
        else [source_id if available else None]
    )
    assert witness.lock_witness(session, product_id, "OpenAPI breaking changes") is available
    assert witness.blocked is (not available)
    if not available:
        before = len(session.mock_calls)
        assert not witness.lock_witness(session, product_id, "OpenAPI breaking changes")
        assert len(session.mock_calls) == before


def test_busy_or_removed_search_witness_fences_remaining_products_without_policy_reads():
    witness, session = PublicSearchContext({"articles": {uuid.uuid4()}}), Mock(spec=Session)
    session.execute.return_value.first.return_value = None
    assert not witness.lock_witness(session, uuid.uuid4(), "OpenAPI")
    assert witness.blocked
    session.scalar.assert_not_called()
