"""Prepared relevance and issued receipts retain their exact public/account context.

All repository rows below are already scoped facts. These tests exercise result
assembly, URL policy, and live receipt guards rather than SQL selection or locks.
"""

import copy
import uuid
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import partner_tools as admin_tools
from devfeed_core import partner_catalog, partner_tools
from devfeed_core import partner_matching as matching
from devfeed_core import partner_placements as placements
from devfeed_core import partner_tracking as tracking
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import PartnerPlacementDelivery, PartnerProductTopic
from devfeed_core.partner_placement_destinations import PlacementDestinations
from devfeed_core.partner_search_context import PublicSearchContext
from devfeed_core.partner_tools import ProductFacts, article_snapshot, validate_result
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy.orm import Session

from tests.admin.partners.test_partner_read_contracts_unit import evaluation
from tests.support.partner_accounts import account, asset, campaign
from tests.support.partner_matching import NOW, article, product, result, topic


def prepared(value, context, score=100, **changes):
    return PartnerProductTopic(
        **{
            "product_id": value.id,
            "topic_id": context.id,
            "score": score,
            "reason": f"Verified support for {context.name}.",
            "product_revision": value.revision,
            "product_updated_at": value.updated_at,
            "topic_updated_at": context.updated_at,
            **changes,
        }
    )


@pytest.mark.parametrize("available", [False, True])
def test_topic_context_maps_current_prepared_support_or_stops_when_topic_is_unavailable(available):
    context, value, session = topic("OpenAPI"), product(), Mock(spec=Session)
    session.scalar.return_value = context if available else None
    session.execute.return_value.scalars.return_value = [prepared(value, context)]
    output = placements.contextual_candidates(
        session, PlacementQuery(surface="topic", topic_id=context.id), NOW
    )
    assert output == (
        {value.id: (100, "Verified support for OpenAPI.", value.revision, value.updated_at)}
        if available
        else {}
    )
    if not available:
        session.execute.assert_not_called()


@pytest.mark.parametrize("surface", ["feed", "header"])
def test_personalization_strengthens_loaded_context_without_adding_unrelated_inventory(surface):
    context, other = topic("OpenAPI"), topic("Rust")
    value, session, user_id = product(), Mock(spec=Session), uuid.uuid4()
    row = prepared(value, context)
    # Loaded visible article support is 40%; following that topic contributes
    # another 10 points. A stronger unrelated Rust preference adds no inventory.
    session.scalars.return_value = [context.id, other.id]
    session.execute.side_effect = [
        result([(context.id, "supporting", 0.5)]),
        result([]),
        Mock(scalars=Mock(return_value=[row])),
    ]
    query = PlacementQuery(
        surface=surface,
        article_ids=[article().id],
        **({"ad_formats": ["header_sponsorship"]} if surface == "header" else {}),
    )
    output = placements.contextual_candidates(session, query, NOW, user_id)
    assert output == {value.id: (50, row.reason, value.revision, value.updated_at)}
    assert len(output) == 1


@pytest.mark.parametrize("kind", ["articles", "topics", "sources", "tags"])
def test_loaded_search_results_use_the_exact_public_witness_before_mapping_candidates(kind):
    value, identifier, session = product(), uuid.uuid4(), Mock(spec=Session)
    context = PublicSearchContext({kind: {identifier}})
    query = PlacementQuery(
        surface="search",
        ad_formats=["engagement"],
        search={"query": "OpenAPI changes", "results": {kind: [identifier]}},
    )
    session.execute.return_value = [
        (value.id, 100, "Verified support for OpenAPI.", value.revision, value.updated_at)
    ]
    assert placements.contextual_candidates(session, query, NOW, search_context=context) == {
        value.id: (100, "Verified support for OpenAPI.", value.revision, value.updated_at)
    }
    session.scalar.assert_not_called()
    session.execute.assert_called_once()


@pytest.mark.parametrize("context", ["empty-stories", "anonymous", "weak", "feed", "personal"])
def test_promoted_story_needs_its_own_visible_or_personal_topic_support(context):
    session, context_topic, story_id = Mock(spec=Session), topic("OpenAPI"), article().id
    query = PlacementQuery(
        surface="feed", article_ids=[article().id] if context in {"feed", "weak"} else []
    )
    user_id = uuid.uuid4() if context in {"personal", "feed"} else None
    if context in {"feed", "weak"}:
        session.execute.return_value.all.return_value = [
            (context_topic.id, "supporting", 0.5 if context == "feed" else 0.49)
        ]
        session.scalars.return_value = [story_id] if context == "feed" else []
    elif context == "personal":
        session.scalars.side_effect = [[context_topic.id], [story_id]]
        session.execute.return_value.all.return_value = []
    assert placements.sponsored_story_matches(
        session, query, set() if context == "empty-stories" else {story_id}, NOW, user_id
    ) == ({story_id} if context in {"feed", "personal"} else set())
    if context in {"anonymous", "empty-stories", "weak"}:
        session.scalars.assert_not_called()
    elif context == "feed":
        # Visible feed context is authoritative even for a signed-in reader.
        session.execute.assert_called_once()


def test_validated_evaluation_publishes_positive_and_negative_decisions_with_their_origin(
    monkeypatch,
):
    value, tutorial = product(), article()
    news = article(content_type="news", summary="A new OpenAPI release was announced this week.")
    job = evaluation(value, tutorial)
    job.snapshot["articles"].append(article_snapshot(news))
    positive = job.result["decisions"][0]
    negative = {
        "article_id": str(news.id),
        "relevant": False,
        "reason": "Release news does not describe a tutorial task this tool can perform.",
        "article_quote": "",
        "technology": "",
        "evidence_index": 0,
    }
    job.result = validate_result(job.snapshot, {"decisions": [positive, negative]})
    session, publish = Mock(spec=Session), Mock(return_value=True)
    session.get.return_value = value
    session.scalars.return_value = [tutorial, news]
    monkeypatch.setattr(matching, "save_article_match", publish)
    matching.save_evaluation_matches(session, job)
    assert publish.call_count == 2
    for call, story, decision in zip(
        publish.call_args_list, [tutorial, news], job.result["decisions"], strict=True
    ):
        assert call.args == (
            session,
            value,
            story,
            decision["relevant"],
            100 if decision["relevant"] else 0,
            decision["reason"],
        )
        assert call.kwargs == {"origin": "evaluation"}


@pytest.mark.parametrize("destination", ["canonical", "listing", "manual", "unavailable", "story"])
def test_destination_preflight_retains_real_listing_fallbacks_and_memoizes_unavailable_pairs(
    destination,
):
    owner = account(
        outbound_destination="product_url" if destination == "canonical" else "listing_url"
    )
    value, session = product(), Mock(spec=Session)
    creative = asset(owner, value)
    agreement = None
    if destination == "story":
        agreement = campaign(owner, ad_format="sponsored_content", article_id=article().id)
        creative = asset(
            owner, value, agreement, ad_format="sponsored_content", article_id=agreement.article_id
        )
    row, pair = (creative, owner, value, agreement), (owner.id, value.id)
    if destination == "listing":
        session.execute.return_value = [(owner.id, value.id, "https://platform.example/listing")]
    elif destination == "manual":
        session.execute.side_effect = [[], [(owner.id, value.id, value.product_url)]]
    elif destination == "unavailable":
        session.execute.side_effect = [[], []]
    check = PlacementDestinations(session)
    assert check.eligible_rows([row]) == ([] if destination == "unavailable" else [row])
    assert check.unavailable(*pair) is (destination == "unavailable")
    if destination == "unavailable":
        previous = session.execute.call_count
        assert check.eligible_rows([row]) == []
        assert session.execute.call_count == previous
    if destination in {"canonical", "story"}:
        session.execute.assert_not_called()


@pytest.mark.parametrize(
    "guard",
    ["current", "missing-reservation", "wrong-asset", "wrong-time", "paused", "policy-off", "busy"],
)
def test_signed_delivery_events_require_the_exact_issued_reservation_and_current_policy(
    monkeypatch, guard
):
    owner, value = account(), product()
    creative = asset(owner, value)
    signer = tracking.PartnerTrackingTokens(
        "disposable-unit-partner-tracking-key", clock=NOW.timestamp
    )
    receipt = signer.verify(signer.issue(creative.id))
    assert receipt is not None
    issued = PartnerPlacementDelivery(
        delivery_id=receipt.delivery_id,
        asset_id=uuid.uuid4() if guard == "wrong-asset" else creative.id,
        issued_at=NOW - timedelta(seconds=1) if guard == "wrong-time" else NOW,
        surface="feed",
    )
    if guard == "paused":
        owner.status = "paused"
    elif guard == "policy-off":
        owner.organic_enabled = False

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (NOW + timedelta(seconds=30)).astimezone(tz)

    monkeypatch.setattr(tracking, "datetime", Clock)
    session = Mock(spec=Session)
    # Ownership/policy reads are already-scoped repository results, not a lock or
    # SQL eligibility simulation. Every central policy predicate remains real.
    session.scalar.side_effect = [
        owner.id,
        None if guard == "busy" else owner,
        creative,
        value,
        uuid.uuid4(),
        None,
        None,
        receipt.delivery_id,
    ]
    session.get.return_value = None if guard == "missing-reservation" else issued
    if guard == "current":
        assert tracking.record_delivery_event(session, receipt, "impression")
        values = session.execute.call_args.args[0].compile().params
        assert values["asset_id"] == creative.id and values["day"] == NOW.date()
        assert values["impressions"] == 1 and values["clicks"] == 0
    else:
        with pytest.raises(
            tracking.BusyPartnerAsset if guard == "busy" else tracking.InactivePartnerAsset
        ):
            tracking.record_delivery_event(session, receipt, "impression")
        session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_completed_evaluation_expires_when_its_developer_qualification_expires(monkeypatch):
    value, session = product(), Mock(spec=Session)
    story = article()
    prior = evaluation(value, story)
    session.get.return_value = value
    session.execute.return_value.all.return_value = [story]
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW + partner_tools.QUALIFICATION_TTL)
    source_read = Mock(return_value=True)
    monkeypatch.setattr(partner_catalog, "product_has_active_source", source_read)
    output = admin_tools.evaluation_view(session, prior)
    assert not output["current"] and output["status"] == "succeeded"
    assert output["result"] == prior.result
    source_read.assert_not_called()
    session.execute.assert_not_called()
    assert prior.finished_at < NOW


def test_requalified_new_product_revision_does_not_reuse_old_evaluation(monkeypatch):
    value, session = product(), Mock(spec=Session)
    story = article()
    prior = evaluation(value, story)
    # A later catalog edit and completed assessment qualify revision 3, while the
    # succeeded shadow evaluation remains bound to revision 2's original facts.
    value.name, value.revision = "API Checker Pro", 3
    value.updated_at = NOW - timedelta(seconds=30)
    value.assessment_revision, value.verified_at = 3, NOW - timedelta(seconds=10)
    session.get.return_value = value
    session.execute.return_value.all.return_value = [story]
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    source_read = Mock(return_value=True)
    monkeypatch.setattr(partner_catalog, "product_has_active_source", source_read)
    assert partner_tools.eligible(value)
    assert not partner_tools.snapshot_current(session, prior)
    assert prior.snapshot["product"]["revision"] == 2
    source_read.assert_not_called()
    session.execute.assert_not_called()


def test_unlocked_evaluation_read_rechecks_provenance_after_loading_qualified_product(monkeypatch):
    value, session = product(), Mock(spec=Session)
    prior = evaluation(value, article())
    session.get.return_value = value
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    # The admin read has no catalog lock: its already-loaded product can predate
    # a concurrent completed last-source withdrawal. The next service read is
    # fresh and says no active provenance. This is not a persisted orphan fixture.
    source_read = Mock(return_value=False)
    monkeypatch.setattr(partner_catalog, "product_has_active_source", source_read)
    output = admin_tools.evaluation_view(session, prior)
    assert output["current"] is False and output["result"] == prior.result
    source_read.assert_called_once_with(session, value.id)
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_evaluation_application_skips_an_article_purged_after_its_input_fence(monkeypatch):
    value, story, session = product(), article(), Mock(spec=Session)
    job = evaluation(value, story)
    output = job.result
    job.result, job.status, job.attempts, job.finished_at = {}, "queued", 0, None
    start_job(job, NOW - timedelta(seconds=10), 360)
    job.result = output  # Validated provider output is awaiting match application.
    session.get.return_value = value
    # The worker's earlier input fence read this snapshot article. Article
    # retention can finish before the separate application read; the evaluation
    # refers to sampled articles in JSON, without an article foreign key.
    session.scalars.return_value = []
    save = Mock()
    monkeypatch.setattr(matching, "save_article_match", save)
    matching.save_evaluation_matches(session, job)
    save.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert job.status == "running" and job.result == output
    assert job.lease_token is not None and job.lease_until > NOW


def test_weaker_later_evidence_cannot_replace_a_stronger_grounded_task_match():
    value, story = product(), article()
    strong = copy.deepcopy(value.evidence[0])
    weak = {
        "url": "https://checker.example/docs/basic",
        "quote": "OpenAPI detects changes before release.",
        "capability": "Detect changes before release.",
    }
    ProductFacts.model_validate(value, from_attributes=True)
    best = matching.article_match(value, story)
    value.evidence = [weak]
    weaker = matching.article_match(value, story)
    assert best[0] and weaker[0] and best[1] > weaker[1]
    value.evidence = [strong, weak]
    assert matching.article_match(value, story) == best
