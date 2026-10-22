"""Relationship admission and review policies over scoped repository results."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import topic_relationships as relationships
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Topic, TopicRelation
from devfeed_core.services import OperationConflict, RecordNotFound
from taxonomy_command_helpers import ACTOR, NOW, relationship_job, relationship_proposal, topic


@pytest.fixture
def runtime(monkeypatch):
    focal, peer = topic(), topic("JavaScript", kind="language")
    current = relationship_job(focal, peer)
    suggestion = relationship_proposal(focal, peer, current)
    session = Mock()
    topics = {focal.id: focal, peer.id: peer}
    session.get.side_effect = lambda model, identifier: (
        topics.get(identifier) if model is Topic else None
    )
    lock, relate = Mock(), Mock()
    monkeypatch.setattr(relationships, "lock_topics", lock)
    monkeypatch.setattr(relationships, "relate_topics", relate)
    monkeypatch.setattr(relationships, "utcnow", lambda: NOW)
    return SimpleNamespace(
        focal=focal,
        peer=peer,
        topics=topics,
        job=current,
        proposal=suggestion,
        session=session,
        lock=lock,
        relate=relate,
    )


def request(value, *, specific=True, candidate_ids=None):
    return relationships.request_relationship_analysis(
        value.session,
        value.focal.id,
        relationships.RelationshipAnalysisRequest(
            related_topic_id=value.peer.id if specific else None
        ),
        ACTOR,
        candidate_ids=candidate_ids,
    )


@pytest.mark.parametrize("race", [False, True])
@pytest.mark.parametrize("specific", [False, True])
def test_existing_matching_work_is_reused_without_new_job_or_research_reset(
    runtime, race, specific
):
    active = relationship_job(runtime.focal, runtime.peer, status="running")
    if not specific:
        active.input_snapshot["related_topic_id"] = None
        active.input_hash = snapshot_hash(active.input_snapshot)
    runtime.session.scalar.side_effect = [None, active] if race else [active]
    before = copy.deepcopy(
        (
            active.input_snapshot,
            active.status,
            active.attempts,
            active.lease_token,
            active.lease_until,
        )
    )
    assert request(runtime, specific=specific) is active
    assert before == (
        active.input_snapshot,
        active.status,
        active.attempts,
        active.lease_token,
        active.lease_until,
    )
    runtime.session.add.assert_not_called()
    runtime.session.scalars.assert_not_called()
    assert runtime.lock.called is race


@pytest.mark.parametrize("race", [False, True])
def test_a_different_requested_peer_does_not_hijack_active_research(runtime, race):
    active = relationship_job(runtime.focal, runtime.peer, status="queued")
    active.input_snapshot["related_topic_id"] = None
    active.input_hash = snapshot_hash(active.input_snapshot)
    runtime.session.scalar.side_effect = [None, active] if race else [active]
    with pytest.raises(OperationConflict, match="already running"):
        request(runtime)
    runtime.session.add.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
def test_research_cannot_target_the_same_topic_on_either_admission_path(runtime, existing):
    active = relationship_job(runtime.focal, runtime.peer, status="queued")
    runtime.session.scalar.return_value = active if existing else None
    with pytest.raises(OperationConflict, match="different related topic"):
        relationships.request_relationship_analysis(
            runtime.session,
            runtime.focal.id,
            relationships.RelationshipAnalysisRequest(related_topic_id=runtime.focal.id),
            ACTOR,
        )
    runtime.session.add.assert_not_called()


@pytest.mark.parametrize("scope", ["empty", "large-catalog", "large-prompt"])
def test_research_requires_a_bounded_nonempty_peer_catalog(runtime, scope):
    runtime.session.scalar.return_value = None
    if scope == "empty":
        peers, message = [], "At least two active"
    elif scope == "large-catalog":
        peers = [topic(f"Technology{index}") for index in range(5001)]
        message = "specific related topic"
    else:
        peers = [
            topic(f"Technology{index}", description="Official developer documentation. " * 55)
            for index in range(130)
        ]
        message = "model limit"
    runtime.session.scalars.side_effect = [SimpleNamespace(all=lambda: peers), [], []]
    with pytest.raises(OperationConflict, match=message):
        request(runtime, specific=False)
    runtime.session.add.assert_not_called()


@pytest.mark.parametrize("specific", [False, True])
def test_new_request_freezes_both_identities_prior_edges_and_the_authenticated_actor(
    runtime, specific
):
    prior = relationship_proposal(
        runtime.focal,
        runtime.peer,
        runtime.job,
        status="rejected",
        reviewed_at=NOW,
        reviewed_by=ACTOR,
    )
    edge = TopicRelation(
        topic_id=runtime.peer.id,
        related_topic_id=runtime.focal.id,
        relation="related_to",
        evidence_url="https://react.dev/learn",
    )
    runtime.session.scalar.return_value = None
    runtime.session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [runtime.peer]),
        [edge],
        [prior],
    ]
    job = request(runtime, specific=specific, candidate_ids=[runtime.peer.id])
    assert job.topic_id == runtime.focal.id and job.requested_by == ACTOR
    assert job.prompt_version == relationships.PROMPT_VERSION
    assert job.input_hash == snapshot_hash(job.input_snapshot)
    assert job.input_snapshot["related_topic_id"] == (str(runtime.peer.id) if specific else None)
    assert job.input_snapshot["snapshots"] == {
        str(row.id): relationships.topic_snapshot(row) for row in (runtime.focal, runtime.peer)
    }
    assert job.input_snapshot["excluded_edges"] == [
        [str(runtime.peer.id), str(runtime.focal.id), "related_to"],
        [str(runtime.focal.id), str(runtime.peer.id), "uses_language"],
    ]
    runtime.session.add.assert_called_once_with(job)
    runtime.session.flush.assert_called_once()
    assert runtime.focal.status == runtime.peer.status == "active"
    assert prior.status == "rejected"


def result_for(value, **changes):
    proposal = value.proposal
    raw = {key: getattr(proposal, key) for key in relationships.RelationshipSuggestion.model_fields}
    return relationships.RelationshipResearchResult(relationships=[{**raw, **changes}], reasons=[])


def test_apply_research_creates_a_pending_evidence_snapshot_without_writing_an_edge(runtime):
    current = relationship_job(runtime.focal, runtime.peer, status="running")
    runtime.session.scalar.return_value = False  # Scoped edge-existence result.
    runtime.session.scalars.return_value = []
    created = []

    def add(row):
        row.id = uuid.uuid4()  # Repository-generated identity only; no SQL semantics.
        created.append(row)

    runtime.session.add.side_effect = add
    state, identifiers = relationships.apply_relationship_research(
        runtime.session, current, result_for(runtime)
    )
    assert state == "enriched" and identifiers == [str(created[0].id)]
    row = created[0]
    assert row.job_id == current.id and row.created_by == ACTOR
    assert row.topic_snapshot == current.input_snapshot["snapshots"][str(runtime.focal.id)]
    assert row.related_topic_snapshot == current.input_snapshot["snapshots"][str(runtime.peer.id)]
    assert row.evidence_quote == runtime.proposal.evidence_quote
    assert row.reviewed_at is None and row.reviewed_by is None
    runtime.relate.assert_not_called()
    runtime.session.delete.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "focal",
        "peer",
        "inactive-peer",
        "deleted-peer",
        "existing-edge",
        "prior-pending",
        "prior-reviewed",
    ],
)
def test_stale_or_already_represented_research_never_creates_another_suggestion(runtime, change):
    current = relationship_job(runtime.focal, runtime.peer, status="running")
    prior = runtime.proposal
    runtime.session.scalar.return_value = change == "existing-edge"
    runtime.session.scalars.return_value = []
    if change == "focal":
        runtime.focal.description = "A changed focal identity."
    elif change == "peer":
        runtime.peer.description = "A changed peer identity."
    elif change == "inactive-peer":
        runtime.peer.status = "rejected"
    elif change == "deleted-peer":
        runtime.topics.pop(runtime.peer.id)
    elif change.startswith("prior-"):
        if change == "prior-reviewed":
            prior.status, prior.reviewed_at, prior.reviewed_by = "rejected", NOW, ACTOR
        runtime.session.scalars.return_value = [prior]
    result = relationships.apply_relationship_research(
        runtime.session, current, result_for(runtime)
    )
    assert result == ("superseded" if change == "focal" else "no_additions", [])
    runtime.session.add.assert_not_called()
    runtime.relate.assert_not_called()


@pytest.mark.parametrize("violation", ["self", "unlisted-id", "no-focal"])
def test_model_result_cannot_introduce_unrequested_endpoints(runtime, violation):
    current = relationship_job(runtime.focal, runtime.peer, status="running")
    options = (
        {"related_topic_id": runtime.focal.id}
        if violation == "self"
        else {"related_topic_id": uuid.uuid4()}
        if violation == "unlisted-id"
        else {"topic_id": uuid.uuid4()}
    )
    with pytest.raises(ValueError, match="focal topic"):
        relationships.apply_relationship_research(
            runtime.session, current, result_for(runtime, **options)
        )
    runtime.session.add.assert_not_called()


@pytest.mark.parametrize(
    "decision,existing", [("approved", False), ("approved", True), ("rejected", False)]
)
def test_review_attributes_the_decision_and_preserves_an_existing_manual_edge(
    runtime, decision, existing
):
    row = runtime.proposal
    runtime.session.scalar.side_effect = [row, existing]
    runtime.session.scalars.return_value = list(runtime.topics.values())
    body = relationships.RelationshipReview(
        decision=decision,
        expected_input_hash=relationships.proposal_hash(row),
        note="Reviewed the official documentation.",
    )
    result = relationships.review_relationship(runtime.session, row.id, body, ACTOR)
    assert result is row and row.status == decision
    assert row.reviewed_at == NOW and row.reviewed_by == ACTOR and row.review_note == body.note
    if decision == "approved" and not existing:
        relation = runtime.relate.call_args.args[1]
        assert (
            relation.topic_id == row.topic_id and relation.related_topic_id == row.related_topic_id
        )
        assert relation.relation == row.relation and relation.evidence_url == row.evidence_url
    else:
        runtime.relate.assert_not_called()
    if decision == "rejected":
        runtime.session.scalars.assert_not_called()
    runtime.session.flush.assert_called_once()


@pytest.mark.parametrize(
    "guard", ["missing", "reviewed", "hash", "topic-inactive", "topic-changed"]
)
def test_review_requires_an_unchanged_pending_proposal_and_current_topic_identity(runtime, guard):
    row = runtime.proposal
    body = relationships.RelationshipReview(
        decision="approved", expected_input_hash=relationships.proposal_hash(row)
    )
    if guard == "reviewed":
        row.status, row.reviewed_at, row.reviewed_by = "rejected", NOW, ACTOR
    elif guard == "hash":
        row.evidence_quote = "React lets you build user interfaces out of individual pieces."
    elif guard == "topic-inactive":
        runtime.peer.status = "proposed"
    elif guard == "topic-changed":
        runtime.peer.website_url = "https://developer.mozilla.org/JavaScript"
    runtime.session.scalar.return_value = None if guard == "missing" else row
    runtime.session.scalars.return_value = list(runtime.topics.values())
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        relationships.review_relationship(runtime.session, row.id, body, ACTOR)
    runtime.relate.assert_not_called()
    runtime.session.flush.assert_not_called()
