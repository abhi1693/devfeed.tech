"""Relationship review tokens and eligibility follow the researched topic identities."""

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.models import Topic, TopicAnalysisJob, TopicRelationProposal
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_relationships import (
    RelationshipResearchResult,
    active_topic,
    approval_blocker,
    proposal_excludes,
    proposal_hash,
    proposal_view,
    research_current,
    topic_snapshot,
)
from pydantic import ValidationError

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def topic(name, *, kind="framework", status="active"):
    return Topic(
        id=uuid.uuid4(),
        name=name,
        slug=name.lower(),
        kind=kind,
        status=status,
        aliases=[],
        description=f"The {name} developer technology.",
        website_url=f"https://{name.lower()}.example/",
    )


@pytest.fixture
def researched():
    source, target = topic("React"), topic("JavaScript", kind="language")
    proposal = TopicRelationProposal(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        topic_id=source.id,
        related_topic_id=target.id,
        relation="uses_language",
        explanation="React components are written in JavaScript.",
        evidence_url="https://react.dev/learn",
        evidence_title="Writing React components",
        evidence_quote="React components are JavaScript functions that return markup.",
        topic_snapshot=topic_snapshot(source),
        related_topic_snapshot=topic_snapshot(target),
        status="pending",
        created_at=NOW - timedelta(hours=1),
        created_by={"subject": "research-requester"},
        reviewed_at=None,
        reviewed_by=None,
        review_note=None,
    )
    return proposal, {source.id: source, target.id: target}


class TopicLookup:
    """Supply already-loaded primary-key rows without implementing query behavior."""

    def __init__(self, topics):
        self.topics = topics

    def get(self, model, identifier):
        assert model is Topic
        return self.topics.get(identifier)


@pytest.mark.parametrize("endpoint", ["topic_id", "related_topic_id"])
@pytest.mark.parametrize("state", ["missing", "proposed", "rejected"])
def test_approval_requires_both_endpoints_to_remain_active(researched, endpoint, state):
    proposal, topics = researched
    identifier = getattr(proposal, endpoint)
    if state == "missing":
        topics.pop(identifier)
    else:
        topics[identifier].status = state
    result = proposal_view(proposal, topics)
    assert not result.can_approve
    assert result.approval_blocker == "Both topics must still be active before approval"
    assert proposal.status == "pending" and proposal.reviewed_at is None


@pytest.mark.parametrize("endpoint", ["topic_id", "related_topic_id"])
@pytest.mark.parametrize("field", ["name", "slug", "kind", "aliases", "description", "website_url"])
def test_topic_identity_changes_require_fresh_research_before_approval(researched, endpoint, field):
    proposal, topics = researched
    current = topics[getattr(proposal, endpoint)]
    changed = {
        "name": current.name + " platform",
        "slug": current.slug + "-platform",
        "kind": "technology",
        "aliases": [current.name + " platform"],
        "description": "A revised description clarifies which developer technology this denotes.",
        "website_url": current.website_url + "official/",
    }
    setattr(current, field, changed[field])
    blocker = approval_blocker(proposal, topics)
    assert (
        blocker == "A topic changed after research. Reject this suggestion and run research again"
    )
    result = proposal_view(proposal, topics)
    assert not result.can_approve and result.approval_blocker == blocker
    assert result.topic_name == proposal.topic_snapshot["name"]
    assert result.related_topic_name == proposal.related_topic_snapshot["name"]


def test_unchanged_pending_proposal_is_reviewable_without_implicitly_approving_it(researched):
    proposal, topics = researched
    result = proposal_view(proposal, topics)
    assert result.can_approve and result.approval_blocker is None
    assert result.status == proposal.status == "pending"
    assert result.content_hash == proposal_hash(proposal)
    assert (
        result.evidence_url == proposal.evidence_url
        and result.evidence_quote == proposal.evidence_quote
    )
    assert result.reviewed_by is None and result.reviewed_at is None


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_reviewed_history_remains_readable_without_offering_another_approval(researched, decision):
    proposal, topics = researched
    proposal.status = decision
    proposal.reviewed_at, proposal.reviewed_by, proposal.review_note = (
        NOW,
        {"subject": "editor"},
        "Reviewed the official documentation.",
    )
    topics[proposal.related_topic_id].description = "The topic evolved after the review."
    result = proposal_view(proposal, topics)
    assert result.status == decision and not result.can_approve and result.approval_blocker is None
    assert result.reviewed_by == {"subject": "editor"}
    assert result.review_note == proposal.review_note and result.reviewed_at == NOW


@pytest.mark.parametrize(
    "field,value",
    [
        ("relation", "related_to"),
        ("explanation", "Official documentation establishes this direct technical relationship."),
        ("evidence_url", "https://react.dev/reference/react"),
        ("evidence_title", "React reference documentation"),
        ("evidence_quote", "React lets you build user interfaces out of individual pieces."),
    ],
)
def test_review_token_changes_when_the_proposed_relationship_or_evidence_changes(
    researched, field, value
):
    proposal, _ = researched
    before = proposal_hash(proposal)
    setattr(proposal, field, value)
    assert proposal_hash(proposal) != before


@pytest.mark.parametrize("snapshot_field", ["topic_snapshot", "related_topic_snapshot"])
def test_review_token_binds_both_snapshots_and_is_independent_of_dictionary_order(
    researched, snapshot_field
):
    proposal, _ = researched
    before = proposal_hash(proposal)
    proposal.topic_snapshot = dict(reversed(list(proposal.topic_snapshot.items())))
    proposal.related_topic_snapshot = dict(reversed(list(proposal.related_topic_snapshot.items())))
    assert proposal_hash(proposal) == before
    setattr(
        proposal,
        snapshot_field,
        {**getattr(proposal, snapshot_field), "description": "The researched definition changed."},
    )
    assert proposal_hash(proposal) != before


def test_pending_suggestions_block_duplicates_until_their_review_is_resolved(researched):
    proposal, topics = researched
    snapshots = {str(identifier): topic_snapshot(value) for identifier, value in topics.items()}
    snapshots[str(proposal.related_topic_id)]["description"] = "The catalog has since changed."
    assert proposal_excludes(proposal, snapshots)


@pytest.mark.parametrize("endpoint", ["topic_id", "related_topic_id"])
def test_rejected_suggestion_is_not_repeated_until_a_researched_identity_changes(
    researched, endpoint
):
    proposal, topics = researched
    proposal.status, proposal.reviewed_at, proposal.reviewed_by = (
        "rejected",
        NOW,
        {"subject": "editor"},
    )
    proposal.review_note = "The source does not establish a direct dependency."
    snapshots = {str(identifier): topic_snapshot(value) for identifier, value in topics.items()}
    assert proposal_excludes(proposal, snapshots)
    snapshots[str(getattr(proposal, endpoint))]["description"] = (
        "A materially changed topic definition."
    )
    assert not proposal_excludes(proposal, snapshots)


def test_topic_snapshot_uses_research_identity_without_operational_or_branding_metadata():
    current = topic("React")
    before = topic_snapshot(current)
    assert before["aliases"] == [] and before["id"] == str(current.id)
    current.managed_logo = {"source_url": "https://images.example/react.png"}
    current.updated_at = NOW
    assert topic_snapshot(current) == before
    assert "managed_logo" not in before and "updated_at" not in before


@pytest.mark.parametrize("state", ["active", "proposed", "rejected", "missing"])
def test_relationship_research_reads_only_existing_active_topics(state):
    current = topic("React", status=state if state != "missing" else "active")
    lookup = TopicLookup({current.id: current} if state != "missing" else {})
    if state == "active":
        assert active_topic(lookup, current.id) is current
    elif state == "missing":
        with pytest.raises(RecordNotFound, match="Topic not found"):
            active_topic(lookup, current.id)
    else:
        with pytest.raises(OperationConflict, match="requires active topics"):
            active_topic(lookup, current.id)


@pytest.mark.parametrize("change", [None, "identity", "rejected", "deleted"])
def test_research_results_are_current_only_for_the_original_active_focal_topic(change):
    current = topic("React")
    peer = topic("JavaScript", kind="language")
    snapshots = {str(value.id): topic_snapshot(value) for value in (current, peer)}
    job = TopicAnalysisJob(
        id=uuid.uuid4(),
        topic_id=current.id,
        status="running",
        attempts=1,
        available_at=NOW - timedelta(minutes=1),
        created_at=NOW - timedelta(minutes=1),
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=5),
        input_snapshot={
            "topic": deepcopy(snapshots[str(current.id)]),
            "snapshots": snapshots,
            "catalog": [
                [str(value.id), value.name, value.slug, value.kind] for value in (current, peer)
            ],
            "excluded_edges": [],
            "related_topic_id": None,
        },
        requested_by={"subject": "research-requester"},
    )
    topics = {current.id: current}
    if change == "identity":
        current.description = "A different technology definition."
    elif change == "rejected":
        current.status = "rejected"
    elif change == "deleted":
        topics.clear()
    assert research_current(TopicLookup(topics), job) is (change is None)


def test_research_reason_budget_accepts_bounded_explanation_and_rejects_unbounded_output():
    accepted = RelationshipResearchResult(relationships=[], reasons=["x" * 500])
    assert accepted.relationships == [] and len(accepted.reasons[0]) == 500
    with pytest.raises(ValidationError, match="Research reasons must be bounded"):
        RelationshipResearchResult(relationships=[], reasons=["x" * 501])
