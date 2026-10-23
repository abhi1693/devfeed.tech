"""Automatic review requires independently verified identity and immutable evidence."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import (
    article_topic_policy,
    relationship_verification,
    topic_analysis,
    topic_verification,
)
from devfeed_core import (
    topic_auto_approval as approval,
)
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Topic, TopicAnalysisJob, TopicRelation
from devfeed_core.research_evidence import VERIFICATION_VERSION, citation_key
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_proposals import snapshot
from devfeed_core.topic_relationships import proposal_hash

from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import (
    ACTOR,
    NOW,
    metadata_job,
    proposal,
    relationship_job,
    relationship_proposal,
    topic,
)


def evidence(citations):
    return {
        "version": VERIFICATION_VERSION,
        "checks": {
            citation_key(url, quote): {"url": url, "quote": quote, "status": "verified"}
            for url, quote in citations
        },
    }


def identity_check(current):
    item = topic_verification.verification_input(current)
    return topic_verification.checked_verdict(
        {
            "proposal_id": item["proposal_id"],
            "input_hash": item["input_hash"],
            "verdict": "supported",
            "relevance": {
                "verdict": "in_scope",
                "sources": [0],
                "reason": "The primary documentation identifies a UI development library.",
            },
            "fields": [
                {
                    "field": field,
                    "supported": True,
                    "sources": [0],
                    "reason": "Primary documentation establishes this library identity.",
                }
                for field in topic_verification.FIELDS
                if item["topic"].get(field)
            ],
            "aliases": [],
            "sources": [
                {
                    "url": "https://react.dev/learn",
                    "quote": "React components are JavaScript functions that return markup.",
                }
            ],
        },
        item,
    )


@pytest.fixture
def metadata(monkeypatch):
    current = proposal(proposed={**proposal().proposed, "kind": "library"})
    extra = {
        "url": "https://react.dev/",
        "title": "React introduction",
        "quote": "React lets you build user interfaces out of individual pieces called components.",
        "fields": ["description"],
    }
    job = metadata_job(current, extra_sources=[extra])
    job.result.update(
        topic_verification=identity_check(current),
        evidence_verification=evidence(
            [(row["url"], row["quote"]) for row in job.result["sources"]]
        ),
    )
    value = SimpleNamespace(
        proposal=current,
        job=job,
        session=MagicMock(),
        settings=configuration(ai_enabled=True, auto_approve_topics=True),
    )
    value.session.get.return_value = current
    monkeypatch.setattr(approval, "get_settings", lambda: value.settings)
    monkeypatch.setattr(article_topic_policy, "get_settings", lambda: value.settings)

    def review(_session, identifier, body, actor):
        assert identifier == current.id and actor == approval.ACTOR
        assert body.expected_input_hash == snapshot_hash(current.proposed)
        applied = Topic(id=uuid.uuid4(), status="active", **body.topic.model_dump())
        current.topic_id, current.applied = applied.id, snapshot(applied)
        current.status, current.reviewed_at, current.reviewed_by = "approved", NOW, actor
        current.review_note = body.note
        return current

    value.review = Mock(side_effect=review)
    monkeypatch.setattr(approval, "review_proposal", value.review)
    return value


def test_verified_metadata_uses_the_normal_review_service_and_retains_research_receipts(metadata):
    before = copy.deepcopy(metadata.job.result)
    approval.auto_approve_research(metadata.session, metadata.job)
    assert (
        metadata.proposal.status == "approved" and metadata.proposal.reviewed_by == approval.ACTOR
    )
    assert metadata.proposal.applied["draft"] == metadata.proposal.proposed
    assert "DEVFEED_AUTO_APPROVE_TOPICS" in metadata.proposal.review_note
    assert str(metadata.job.id) in metadata.proposal.review_note
    assert metadata.job.result == {
        **before,
        "auto_approval": [
            {
                "proposal_id": str(metadata.proposal.id),
                "setting": "DEVFEED_AUTO_APPROVE_TOPICS",
                "status": "approved",
            }
        ],
    }


@pytest.mark.parametrize(
    "guard",
    ["disabled", "missing-proposal", "already-reviewed", "article-paused", "no-independent-review"],
)
def test_successful_research_alone_cannot_authorize_automatic_review(metadata, guard):
    if guard == "disabled":
        metadata.settings = configuration(ai_enabled=True, auto_approve_topics=False)
    elif guard == "missing-proposal":
        metadata.session.get.return_value = None
    elif guard == "already-reviewed":
        metadata.proposal.status, metadata.proposal.reviewed_at, metadata.proposal.reviewed_by = (
            "rejected",
            NOW,
            ACTOR,
        )
    elif guard == "article-paused":
        metadata.proposal.origin = "article_enrichment"
        metadata.settings = configuration(
            ai_enabled=True, auto_approve_topics=True, article_topic_proposals_enabled=False
        )
    else:
        del metadata.job.result["topic_verification"]
    before = copy.deepcopy(metadata.job.result)
    approval.auto_approve_research(metadata.session, metadata.job)
    metadata.review.assert_not_called()
    assert metadata.job.result == before


@pytest.mark.parametrize(
    "status,outcome",
    [("queued", None), ("running", None), ("failed", None), ("succeeded", "insufficient_evidence")],
)
def test_non_successful_metadata_attempt_cannot_trigger_approval(metadata, status, outcome):
    current = metadata.proposal
    attempted = TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=current.id,
        status=status,
        attempts=0 if status == "queued" else 1,
        created_at=NOW - timedelta(minutes=10),
        available_at=NOW - timedelta(minutes=9),
        finished_at=NOW - timedelta(minutes=1) if status in {"failed", "succeeded"} else None,
        lease_token=uuid.uuid4() if status == "running" else None,
        lease_until=NOW + timedelta(minutes=5) if status == "running" else None,
        input_snapshot={
            "topic": copy.deepcopy(current.proposed),
            "evidence": copy.deepcopy(current.evidence),
        },
        input_hash=snapshot_hash(current.proposed),
        requested_by=ACTOR,
        prompt_version=metadata.job.prompt_version,
        result={},
        outcome=outcome,
    )
    if status == "succeeded":
        attempted.result = topic_analysis.TopicResearchResult(
            outcome="insufficient_evidence",
            kind=None,
            description=None,
            aliases=[],
            keywords=[],
            website_url=None,
            logo_url=None,
            facts=[],
            sources=[],
            reasons=["Primary evidence could not resolve the missing metadata."],
        ).model_dump(mode="json")
    before = copy.deepcopy(attempted.result)
    approval.auto_approve_research(metadata.session, attempted)
    metadata.review.assert_not_called()
    assert attempted.result == before


@pytest.mark.parametrize(
    "invalid", ["later-human-edit", "unsupported-identity", "unverified-research-citation"]
)
def test_automatic_review_is_blocked_by_stale_identity_or_unverified_research(metadata, invalid):
    if invalid == "later-human-edit":
        metadata.proposal.proposed = {**metadata.proposal.proposed, "keywords": ["human-reviewed"]}
        expected = "changed after research"
    elif invalid == "unsupported-identity":
        check = copy.deepcopy(metadata.job.result["topic_verification"]["check"])
        check["verdict"] = "unsupported"
        check["fields"][0]["supported"] = False
        metadata.job.result["topic_verification"] = topic_verification.checked_verdict(
            check, topic_verification.verification_input(metadata.proposal)
        )
        expected = "require verification"
    else:
        # Independent identity cites /learn; the other original research citation failed its fetch.
        row = metadata.job.result["sources"][1]
        metadata.job.result["evidence_verification"]["checks"][
            citation_key(row["url"], row["quote"])
        ].update(status="unverified", reason="http_error", retryable=True)
        expected = "citations could not be verified"
    before = copy.deepcopy(metadata.proposal.proposed)
    approval.auto_approve_research(metadata.session, metadata.job)
    assert metadata.proposal.status == "pending" and metadata.proposal.proposed == before
    metadata.review.assert_not_called()
    audit = metadata.job.result["auto_approval"][-1]
    assert audit["status"] == "blocked" and expected in audit["reason"]
    assert metadata.job.status == "succeeded" and metadata.job.outcome == "enriched"


@pytest.mark.parametrize(
    "error", [OperationConflict("Identity already exists"), RecordNotFound("Topic removed")]
)
def test_review_conflict_does_not_erase_successful_research(metadata, error):
    metadata.review.side_effect = error
    before = copy.deepcopy(metadata.job.result)
    approval.auto_approve_research(metadata.session, metadata.job)
    assert {
        key: value for key, value in metadata.job.result.items() if key != "auto_approval"
    } == before
    assert metadata.job.result["auto_approval"][-1]["reason"] == str(error)
    assert metadata.proposal.status == "pending"


@pytest.fixture
def graph(monkeypatch):
    focal, peer = topic(kind="library"), topic("JavaScript", kind="language")
    job = relationship_job(focal, peer)
    row = relationship_proposal(focal, peer, job)
    item = relationship_verification.verification_input(row)
    job.result["relationship_verification"] = relationship_verification.checked_verdicts(
        {
            "decisions": [
                {
                    "proposal_id": item["proposal_id"],
                    "input_hash": item["input_hash"],
                    "verdict": "supported",
                    "exact_entities": True,
                    "direct_relationship": True,
                    "correct_type_and_direction": True,
                    "evidence_supports_claim": True,
                    "scope_matches": True,
                    "reason": "React documentation identifies components as JavaScript functions.",
                }
            ]
        },
        [item],
    )
    job.result["evidence_verification"] = evidence([(row.evidence_url, row.evidence_quote)])
    value = SimpleNamespace(
        job=job,
        row=row,
        session=MagicMock(),
        settings=configuration(ai_enabled=True, auto_approve_topic_relationships=True),
    )
    value.session.get.return_value = row
    monkeypatch.setattr(approval, "get_settings", lambda: value.settings)

    def review(_session, identifier, body, actor):
        assert identifier == row.id and body.expected_input_hash == proposal_hash(row)
        row.status, row.reviewed_at, row.reviewed_by = "approved", NOW, actor
        row.review_note = body.note
        return row

    value.review = Mock(side_effect=review)
    monkeypatch.setattr(approval, "review_relationship", value.review)
    return value


def test_relationship_approval_requires_both_verified_evidence_and_independent_semantics(graph):
    before = copy.deepcopy(graph.job.result)
    approval.auto_approve_research(graph.session, graph.job)
    assert graph.row.status == "approved" and graph.row.reviewed_by == approval.ACTOR
    assert "DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS" in graph.row.review_note
    assert graph.job.result == {
        **before,
        "auto_approval": [
            {
                "proposal_id": str(graph.row.id),
                "setting": "DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS",
                "status": "approved",
            }
        ],
    }


@pytest.mark.parametrize(
    "guard",
    [
        "disabled",
        "no-verifier",
        "missing-proposal",
        "already-reviewed",
        "unverified-citation",
        "unsupported-direction",
    ],
)
def test_relationship_auto_review_preserves_pending_or_reviewed_state_without_independent_proof(
    graph, guard
):
    if guard == "disabled":
        graph.settings = configuration(ai_enabled=True, auto_approve_topic_relationships=False)
    elif guard == "no-verifier":
        del graph.job.result["relationship_verification"]
    elif guard == "missing-proposal":
        graph.session.get.return_value = None
    elif guard == "already-reviewed":
        graph.row.status, graph.row.reviewed_at, graph.row.reviewed_by = "rejected", NOW, ACTOR
    elif guard == "unverified-citation":
        graph.job.result["evidence_verification"]["checks"][
            citation_key(graph.row.evidence_url, graph.row.evidence_quote)
        ].update(status="unverified", reason="quote_not_found", retryable=False)
    else:
        check = graph.job.result["relationship_verification"]["checks"][str(graph.row.id)]
        check.update(verdict="unsupported", correct_type_and_direction=False)
    before = graph.row.status
    approval.auto_approve_research(graph.session, graph.job)
    graph.review.assert_not_called()
    assert graph.row.status == before
    if guard in {"unverified-citation", "unsupported-direction"}:
        assert graph.job.result["auto_approval"][-1]["status"] == "blocked"
    else:
        assert "auto_approval" not in graph.job.result


@pytest.fixture
def correction(graph, monkeypatch):
    approval.auto_approve_research(graph.session, graph.job)
    graph.edge = TopicRelation(
        topic_id=graph.row.topic_id,
        related_topic_id=graph.row.related_topic_id,
        relation=graph.row.relation,
        evidence_url=graph.row.evidence_url,
    )
    graph.session.scalar.side_effect = [graph.job, None]
    graph.session.scalars.return_value.all.return_value = [graph.edge]
    graph.lock = Mock()
    monkeypatch.setattr(approval, "lock_topics", graph.lock)
    monkeypatch.setattr(approval, "utcnow", lambda: NOW + timedelta(minutes=1))
    graph.token = proposal_hash(graph.row)
    return graph


def retract(value):
    approval.retract_reviewed_relationship(
        value.session,
        value.row.id,
        expected_hash=value.token,
        expected_reviewer=approval.ACTOR,
        expected_reviewed_at=NOW,
        note="The reviewed source refers to a different package.",
        actor=ACTOR,
    )


def test_operator_correction_keeps_the_original_approval_audit_and_exact_removed_edge(correction):
    before = copy.deepcopy(correction.job.result)
    previous_note = correction.row.review_note
    retract(correction)
    correction.session.delete.assert_called_once_with(correction.edge)
    assert correction.row.status == "rejected" and correction.row.reviewed_by == ACTOR
    assert correction.row.reviewed_at == NOW + timedelta(minutes=1)
    audit = correction.job.result["corrections"][-1]
    assert (
        audit["proposal_id"] == str(correction.row.id) and audit["input_hash"] == correction.token
    )
    assert (
        audit["previous_status"] == "approved" and audit["previous_reviewed_by"] == approval.ACTOR
    )
    assert (
        audit["previous_reviewed_at"] == NOW.isoformat()
        and audit["previous_review_note"] == previous_note
    )
    assert audit["actor"] == ACTOR and audit["reason"] == correction.row.review_note
    assert audit["removed_edge"] == {
        "topic_id": str(correction.edge.topic_id),
        "related_topic_id": str(correction.edge.related_topic_id),
        "relation": correction.edge.relation,
        "evidence_url": correction.edge.evidence_url,
    }
    assert {
        key: value for key, value in correction.job.result.items() if key != "corrections"
    } == before


@pytest.mark.parametrize(
    "guard",
    [
        "missing-proposal",
        "review-changed",
        "review-time",
        "evidence-hash",
        "already-corrected",
        "missing-edge",
        "changed-edge",
        "other-review",
    ],
)
def test_correction_is_bound_to_the_inspected_review_and_unchanged_source(correction, guard):
    if guard == "missing-proposal":
        correction.session.get.return_value = None
    elif guard == "review-changed":
        correction.row.reviewed_by = ACTOR
    elif guard == "review-time":
        correction.row.reviewed_at = NOW + timedelta(seconds=10)
    elif guard == "evidence-hash":
        correction.row.explanation = "A later review changed the proposed relationship explanation."
    elif guard == "already-corrected":
        correction.row.status = "rejected"
    elif guard == "missing-edge":
        correction.session.scalars.return_value.all.return_value = []
    elif guard == "changed-edge":
        correction.edge.evidence_url = "https://react.dev/reference/react"
    else:
        correction.session.scalar.side_effect = [correction.job, uuid.uuid4()]
    before = copy.deepcopy(correction.job.result)
    with pytest.raises(RecordNotFound if guard == "missing-proposal" else OperationConflict):
        retract(correction)
    correction.session.delete.assert_not_called()
    assert correction.job.result == before


@pytest.mark.parametrize("note", [" ", "x" * 1001])
def test_correction_requires_a_bounded_operator_reason(correction, note):
    with pytest.raises(ValueError, match="bounded correction reason"):
        approval.retract_automatic_relationship(
            correction.session,
            correction.row.id,
            expected_hash=correction.token,
            note=note,
            actor=ACTOR,
        )
    correction.session.delete.assert_not_called()
