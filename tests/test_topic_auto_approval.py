"""Automatic taxonomy review preserves validation, evidence and transaction boundaries."""

import uuid

import pytest
from devfeed_aggregator import topic_analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import topic_auto_approval
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelation,
    TopicRelationProposal,
)
from devfeed_core.services import OperationConflict
from sqlalchemy import select
from test_topic_analysis import (
    output,
    prepare_relationship_catalog,
    relationship_jobs,
    run_metadata_research,
)
from test_topic_analysis import (
    pending as pending,
)
from test_topic_relationships import (
    catalog as catalog,
)
from test_topic_relationships import (
    execute,
    queue,
    suggestion,
)

pytestmark = pytest.mark.integration


def configure(monkeypatch, *, topics=False, relationships=False):
    monkeypatch.setenv("DEVFEED_AUTO_APPROVE_TOPICS", str(topics).lower())
    monkeypatch.setenv("DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS", str(relationships).lower())
    get_settings.cache_clear()


@pytest.mark.parametrize("enabled", [False, True])
def test_topic_policy_approves_only_after_research_and_chains_relationships(
    admin_client, database, pending, monkeypatch, enabled
):
    configure(monkeypatch, topics=enabled, relationships=not enabled)
    with database.begin() as session:
        session.add(Topic(name="Peer", slug="peer", kind="technology", status="active"))
    assert admin_client.get(f"/v1/admin/topic-proposals/{pending}").json()["status"] == "pending"
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    topic_analysis_tasks._analyze(identifier)  # Duplicate delivery must not review twice.
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        job = session.get(TopicAnalysisJob, identifier)
        assert job.status == "succeeded" and job.outcome == "enriched"
        assert proposal.evidence[-1]["provider"] == "ai_topic_research"
        if enabled:
            assert proposal.status == "approved" and proposal.reviewed_at
            assert proposal.reviewed_by == topic_auto_approval.ACTOR
            assert "DEVFEED_AUTO_APPROVE_TOPICS" in proposal.review_note
            assert session.get(Topic, proposal.topic_id).status == "active"
            assert session.get(Topic, proposal.topic_id).keywords == ["example-specific"]
            assert job.result["auto_approval"] == [
                {
                    "proposal_id": pending,
                    "setting": "DEVFEED_AUTO_APPROVE_TOPICS",
                    "status": "approved",
                }
            ]
            followup = session.get(
                TopicAnalysisJob, uuid.UUID(job.result["relationship_analysis_id"])
            )
            assert followup.topic_id == proposal.topic_id and followup.status == "queued"
        else:
            assert proposal.status == "pending" and proposal.topic_id is None
            assert "auto_approval" not in job.result
    assert len(relationship_jobs(database)) == (1 if enabled else 0)


@pytest.mark.parametrize("conflict", ["duplicate", "stale_update"])
def test_topic_approval_conflict_preserves_enrichment_for_manual_review(
    admin_client, database, pending, monkeypatch, conflict
):
    configure(monkeypatch, topics=True)
    if conflict == "duplicate":
        with database.begin() as session:
            session.add(Topic(name="Example", slug="example", kind="technology", status="active"))
    else:
        identifier = prepare_relationship_catalog(database, pending)
        with database.begin() as session:
            session.get(Topic, identifier).description = "Changed after proposal"
    job_id = run_metadata_research(admin_client, pending, monkeypatch)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        job = session.get(TopicAnalysisJob, job_id)
        assert job.status == "succeeded" and job.outcome == "enriched" and job.error is None
        assert proposal.status == "pending" and proposal.reviewed_by is None
        assert proposal.proposed["keywords"] == ["example-specific"]
        assert proposal.evidence[-1]["analysis_id"] == str(job.id)
        (review,) = job.result["auto_approval"]
        assert review["status"] == "blocked" and review["proposal_id"] == pending
        assert ("already exists" if conflict == "duplicate" else "Topic changed") in review[
            "reason"
        ]


@pytest.mark.parametrize(
    "failure", ["insufficient_evidence", "uncited", "timeout", "stale", "rejected"]
)
def test_auto_approval_never_bypasses_research_or_review_guards(
    admin_client, database, pending, monkeypatch, failure
):
    configure(monkeypatch, topics=True, relationships=True)
    result = output()
    if failure == "insufficient_evidence":
        result["outcome"] = "insufficient_evidence"
    elif failure == "uncited":
        result["sources"] = []

    def during():
        if failure == "timeout":
            raise AnalysisError("codex_timeout")
        if failure == "rejected":
            admin_client.post(
                f"/v1/admin/topic-proposals/{pending}/review", json={"decision": "rejected"}
            ).raise_for_status()
        elif failure == "stale":
            with database.begin() as session:
                proposal = session.get(TopicProposal, uuid.UUID(pending))
                proposal.proposed = {**proposal.proposed, "keywords": ["human"]}

    identifier = run_metadata_research(
        admin_client, pending, monkeypatch, result=result, during=during
    )
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        job = session.get(TopicAnalysisJob, identifier)
        assert proposal.status == ("rejected" if failure == "rejected" else "pending")
        assert proposal.topic_id is None
        assert job.outcome != "enriched"
        assert not job.result or "auto_approval" not in job.result
        assert session.scalars(select(Topic)).all() == []
    assert relationship_jobs(database) == []


@pytest.mark.parametrize("enabled", [False, True])
def test_relationship_policy_approves_current_run_once_and_keeps_evidence(
    admin_client, database, catalog, monkeypatch, enabled
):
    configure(monkeypatch, topics=not enabled, relationships=enabled)
    job_id = queue(admin_client, catalog[0])
    execute(monkeypatch, job_id, [suggestion(catalog)])
    topic_analysis_tasks._analyze(job_id)
    with database() as session:
        proposal = session.scalar(select(TopicRelationProposal))
        job = session.get(TopicAnalysisJob, job_id)
        edges = session.scalars(select(TopicRelation)).all()
        assert job.status == "succeeded" and job.outcome == "enriched"
        assert proposal.evidence_url == "https://react.dev/"
        if enabled:
            assert proposal.status == "approved" and proposal.reviewed_at
            assert proposal.reviewed_by == topic_auto_approval.ACTOR
            assert "DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS" in proposal.review_note
            assert len(edges) == 1 and edges[0].evidence_url == proposal.evidence_url
            assert len(job.result["auto_approval"]) == 1
        else:
            assert proposal.status == "pending" and proposal.reviewed_by is None
            assert edges == [] and "auto_approval" not in job.result


@pytest.mark.parametrize("change", ["inactive", "stale", "empty"])
def test_relationship_auto_approval_requires_current_active_topics_and_new_suggestions(
    admin_client, database, catalog, monkeypatch, change
):
    configure(monkeypatch, relationships=True)
    job_id = queue(admin_client, catalog[0])

    def during():
        with database.begin() as session:
            topic = session.get(Topic, catalog[1])
            if change == "inactive":
                topic.status = "rejected"
            elif change == "stale":
                topic.description = "Changed during research"

    execute(monkeypatch, job_id, [] if change == "empty" else [suggestion(catalog)], during=during)
    with database() as session:
        job = session.get(TopicAnalysisJob, job_id)
        assert job.status == "succeeded" and job.outcome == "no_additions"
        assert "auto_approval" not in job.result
        assert session.scalars(select(TopicRelation)).all() == []
        assert session.scalars(select(TopicRelationProposal)).all() == []


def test_one_blocked_relationship_does_not_undo_research_or_other_approvals(
    admin_client, database, catalog, monkeypatch
):
    configure(monkeypatch, relationships=True)
    original = topic_auto_approval.review_relationship

    def conflicting_review(session, identifier, body, actor):
        proposal = original(session, identifier, body, actor)
        if proposal.related_topic_id == catalog[1]:
            # Exercise rollback after an actual edge and review have been flushed.
            raise OperationConflict("This relationship needs manual review")
        return proposal

    monkeypatch.setattr(topic_auto_approval, "review_relationship", conflicting_review)
    job_id = queue(admin_client, catalog[0])
    execute(
        monkeypatch,
        job_id,
        [
            suggestion(catalog),
            suggestion(catalog, related_topic_id=str(catalog[2]), relation="related_to"),
        ],
    )
    with database() as session:
        job = session.get(TopicAnalysisJob, job_id)
        proposals = {
            proposal.related_topic_id: proposal
            for proposal in session.scalars(select(TopicRelationProposal))
        }
        edges = session.scalars(select(TopicRelation)).all()
        assert job.status == "succeeded" and job.outcome == "enriched" and job.error is None
        assert (
            proposals[catalog[1]].status == "pending" and proposals[catalog[1]].reviewed_by is None
        )
        assert proposals[catalog[2]].status == "approved"
        assert len(edges) == 1 and edges[0].related_topic_id == catalog[2]
        assert [entry["status"] for entry in job.result["auto_approval"]] == ["blocked", "approved"]


def test_both_policies_complete_topic_and_relationship_approval_chain(
    admin_client, database, pending, monkeypatch
):
    configure(monkeypatch, topics=True, relationships=True)
    with database.begin() as session:
        peer = Topic(name="Peer", slug="peer", kind="technology", status="active")
        session.add(peer)
        session.flush()
        peer_id = peer.id
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    (followup,) = relationship_jobs(database)
    execute(
        monkeypatch, followup.id, [suggestion([followup.topic_id, peer_id], relation="related_to")]
    )
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert proposal.status == "approved"
        assert session.get(Topic, proposal.topic_id).status == "active"
        relationship = session.scalar(select(TopicRelationProposal))
        assert relationship.status == "approved" and relationship.topic_id == proposal.topic_id
        assert session.scalar(select(TopicRelation)).related_topic_id == peer_id
        for job_id in (identifier, followup.id):
            job = session.get(TopicAnalysisJob, job_id)
            assert (
                job.status == "succeeded" and job.result["auto_approval"][0]["status"] == "approved"
            )
