"""Verification recovery is bounded, restart-safe and subordinate to editorial decisions."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import research_verification_tasks as tasks
from devfeed_aggregator import scheduler, topic_analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core.config import get_settings
from devfeed_core.models import (
    ResearchVerificationJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelation,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.relationship_verification import (
    checked_verdicts,
    relationship_verified,
    verification_input,
)
from devfeed_core.research_evidence import VERIFICATION_VERSION, citation_key
from devfeed_core.research_verification import schedule_verification
from devfeed_core.services import OperationConflict
from devfeed_core.topic_auto_approval import auto_approve_research, retract_automatic_relationship
from devfeed_core.topic_relationships import proposal_hash, relationship_prompt
from sqlalchemy import select
from test_topic_analysis import pending as pending
from test_topic_analysis import run_metadata_research
from test_topic_relationships import catalog as catalog
from test_topic_relationships import execute, queue, suggestion, verify


def enable(monkeypatch, *, relationships=False):
    monkeypatch.setenv(
        "DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS"
        if relationships
        else "DEVFEED_AUTO_APPROVE_TOPICS",
        "true",
    )
    get_settings.cache_clear()


def evidence(citations, *, reason=None, **metadata):
    return {
        "version": VERIFICATION_VERSION,
        "checks": {
            citation_key(url, quote): {
                "url": url,
                "quote": quote,
                "status": "unverified" if reason else "verified",
                "reason": reason,
                **metadata,
            }
            for url, quote in citations
        },
    }


def test_relationship_prompt_supplies_exact_candidate_identity():
    focal = {"id": "1", "name": "AI for science"}
    peer = {
        "id": "2",
        "name": "Molecule",
        "aliases": ["Ansible Molecule"],
        "description": "Tests Ansible roles",
        "website_url": "https://molecule.readthedocs.io/",
    }
    prompt = relationship_prompt(
        {
            "topic": focal,
            "catalog": [["1"], ["2"]],
            "snapshots": {"1": focal, "2": peer},
            "excluded_edges": [],
        }
    )
    assert peer["description"] in prompt and peer["website_url"] in prompt
    assert peer["aliases"][0] in prompt


@pytest.mark.integration
@pytest.mark.parametrize(
    "defect",
    [
        "exact_entities",
        "direct_relationship",
        "correct_type_and_direction",
        "evidence_supports_claim",
        "scope_matches",
        "verdict",
        "input_hash",
        "missing",
        "duplicate",
    ],
)
def test_verified_quote_cannot_bypass_independent_review(
    admin_client, database, catalog, monkeypatch, defect
):
    identifier = queue(admin_client, catalog[0])
    execute(monkeypatch, identifier, [suggestion(catalog)])
    enable(monkeypatch, relationships=True)
    with database.begin() as session:
        proposal = session.scalar(select(TopicRelationProposal))
        item = verification_input(proposal)
        decision = {
            "proposal_id": item["proposal_id"],
            "input_hash": item["input_hash"],
            "verdict": "supported",
            "exact_entities": True,
            "direct_relationship": True,
            "correct_type_and_direction": True,
            "evidence_supports_claim": True,
            "scope_matches": True,
            "reason": "Checked exact project.",
        }
        if defect in {"input_hash", "verdict"}:
            decision[defect] = "0" * 64 if defect == "input_hash" else "unsupported"
        elif defect not in {"missing", "duplicate"}:
            decision[defect] = False
        output = {
            "decisions": []
            if defect == "missing"
            else [decision, decision]
            if defect == "duplicate"
            else [decision]
        }
        if defect in {"input_hash", "missing", "duplicate"}:
            with pytest.raises(ValueError):
                checked_verdicts(output, [item])
            verification = {}
        else:
            verification = checked_verdicts(output, [item])
        assert not relationship_verified(verification, proposal)
        job = session.get(TopicAnalysisJob, identifier)
        job.result = {**job.result, "relationship_verification": verification}
        auto_approve_research(session, job)
        assert proposal.status == "pending"
        assert session.scalar(select(TopicRelation)) is None


@pytest.mark.integration
@pytest.mark.parametrize("legacy", [False, True])
def test_citation_recovery_approves_unchanged_metadata_without_research(
    admin_client, database, pending, monkeypatch, legacy
):
    enable(monkeypatch)
    monkeypatch.setattr(
        topic_analysis_tasks,
        "verify_citations",
        lambda values: evidence(values, reason="transport_error"),
    )
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    with database.begin() as session:
        job = session.get(TopicAnalysisJob, identifier)
        before = job.attempts
        if legacy:
            job.result = {
                key: value for key, value in job.result.items() if key != "applied_input_hash"
            }
    assert schedule_verification(database) == 1
    assert schedule_verification(database) == 0
    fetched = []

    def fetch(values):
        fetched.extend(values)
        return evidence(values)

    monkeypatch.setattr(tasks, "verify_citations", fetch)
    from test_topic_verification import mock_verifier

    mock_verifier(monkeypatch)
    tasks.verify_research(str(identifier))
    tasks.verify_research(str(identifier))
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "approved"
        job = session.get(TopicAnalysisJob, identifier)
        assert job.attempts == before
        assert len(job.result["verification_attempts"]) == 1
        assert session.get(ResearchVerificationJob, identifier).status == "succeeded"
    assert len(fetched) == 1


@pytest.mark.integration
@pytest.mark.parametrize("change", ["edited", "rejected", "legacy_edited", "during_edit"])
def test_recovery_never_approves_modified_or_reviewed_metadata(
    admin_client, database, pending, monkeypatch, change
):
    enable(monkeypatch)
    monkeypatch.setattr(
        topic_analysis_tasks,
        "verify_citations",
        lambda values: evidence(values, reason="transport_error"),
    )
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    assert schedule_verification(database) == 1

    def edit():
        with database.begin() as session:
            proposal = session.get(TopicProposal, uuid.UUID(pending))
            if change == "rejected":
                proposal.status, proposal.reviewed_at, proposal.reviewed_by = (
                    "rejected",
                    utcnow(),
                    {"subject": "editor"},
                )
            else:
                proposal.proposed = {**proposal.proposed, "description": "Operator edit"}
            if change == "legacy_edited":
                job = session.get(TopicAnalysisJob, identifier)
                job.result = {
                    key: value for key, value in job.result.items() if key != "applied_input_hash"
                }

    if change != "during_edit":
        edit()

    def fetch(values):
        if change == "during_edit":
            edit()
        return evidence(values)

    monkeypatch.setattr(tasks, "verify_citations", fetch)
    from test_topic_verification import mock_verifier

    mock_verifier(monkeypatch)
    tasks.verify_research(str(identifier))
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status != "approved"
        assert session.scalar(select(Topic)) is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "reason,retryable",
    [
        ("transport_error", True),
        ("http_error", False),
        ("quote_not_found", False),
        ("unsupported_content_type", False),
    ],
)
def test_retry_backoff_and_exhaustion_preserve_research(
    admin_client, database, pending, monkeypatch, reason, retryable
):
    enable(monkeypatch)
    monkeypatch.setattr(
        topic_analysis_tasks,
        "verify_citations",
        lambda values: evidence(values, reason=reason, retryable=retryable),
    )
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    schedule_verification(database)
    calls = []

    def fetch(values):
        calls.append(values)
        return evidence(values, reason=reason, retryable=retryable, retry_after=1800)

    monkeypatch.setattr(tasks, "verify_citations", fetch)
    for attempt in range(1, 4 if retryable else 2):
        started = utcnow()
        tasks.verify_research(str(identifier))
        with database.begin() as session:
            task = session.get(ResearchVerificationJob, identifier)
            assert task.attempts == attempt
            if retryable and attempt < 3:
                assert task.status == "queued" and task.available_at >= started + timedelta(
                    seconds=1800
                )
                task.available_at = utcnow() - timedelta(seconds=1)
            else:
                assert task.status == ("failed" if retryable else "succeeded")
            assert session.get(TopicAnalysisJob, identifier).status == "succeeded"
            assert session.get(TopicProposal, uuid.UUID(pending)).status == "pending"
    assert len(calls) == (3 if retryable else 0)
    assert schedule_verification(database) == 0


@pytest.mark.integration
def test_expired_verification_lease_recovers_and_routes_queues(
    admin_client, database, catalog, monkeypatch
):
    identifier = queue(admin_client, catalog[0])
    execute(monkeypatch, identifier, [suggestion(catalog)])
    enable(monkeypatch, relationships=True)
    schedule_verification(database)
    calls = []
    queue_fake = SimpleNamespace(
        enqueue=lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(id="rq")
    )
    assert (
        scheduler.dispatch_jobs(
            database, queue_fake, 10, utcnow(), kind="research-verification", relationships=False
        )
        == 0
    )
    assert (
        scheduler.dispatch_jobs(
            database, queue_fake, 10, utcnow(), kind="research-verification", relationships=True
        )
        == 1
    )
    assert calls[0][0][0].endswith("verify_research")
    with database.begin() as session:
        task = session.get(ResearchVerificationJob, identifier)
        task.status, task.attempts, task.lease_token = "running", 1, uuid.uuid4()
        task.lease_until = utcnow() - timedelta(seconds=1)
    assert scheduler.recover_jobs(database, 10, utcnow(), kind="research-verification") == 1
    with database.begin() as session:
        task = session.get(ResearchVerificationJob, identifier)
        assert task.status == "queued" and task.lease_token is None and task.dispatched_at is None
        task.available_at = utcnow()
    verify(monkeypatch, identifier)
    with database() as session:
        assert session.get(ResearchVerificationJob, identifier).status == "succeeded"
        assert session.scalar(select(TopicRelationProposal)).status == "approved"


@pytest.mark.integration
@pytest.mark.parametrize("change", ["identity", "rejected", "lost_lease"])
def test_late_semantic_result_cannot_undo_changes(
    admin_client, database, catalog, monkeypatch, change
):
    identifier = queue(admin_client, catalog[0])
    execute(monkeypatch, identifier, [suggestion(catalog)])
    enable(monkeypatch, relationships=True)

    def during():
        with database.begin() as session:
            if change == "identity":
                session.get(Topic, catalog[1]).description = "Different entity"
            elif change == "rejected":
                proposal = session.scalar(select(TopicRelationProposal))
                proposal.status, proposal.reviewed_at, proposal.reviewed_by = (
                    "rejected",
                    utcnow(),
                    {"subject": "editor"},
                )
            else:
                session.get(ResearchVerificationJob, identifier).lease_token = uuid.uuid4()

    verify(monkeypatch, identifier, during=during)
    with database() as session:
        assert session.scalar(select(TopicRelation)) is None


@pytest.mark.integration
def test_correction_preserves_original_review_and_refuses_changed_edges(
    admin_client, database, catalog, monkeypatch
):
    enable(monkeypatch, relationships=True)
    identifier = queue(admin_client, catalog[0])
    execute(monkeypatch, identifier, [suggestion(catalog)])
    actor = {"subject": "authorized-operator"}
    with database.begin() as session:
        proposal = session.scalar(select(TopicRelationProposal))
        original = proposal.review_note
        edge = session.scalar(select(TopicRelation))
        edge.evidence_url = "https://different.example/"
        session.flush()
        with pytest.raises(OperationConflict):
            retract_automatic_relationship(
                session,
                proposal.id,
                expected_hash=proposal_hash(proposal),
                note="Wrong entity",
                actor=actor,
            )
        edge.evidence_url = proposal.evidence_url
        session.flush()
        retract_automatic_relationship(
            session,
            proposal.id,
            expected_hash=proposal_hash(proposal),
            note="Wrong entity",
            actor=actor,
        )
    with database() as session:
        assert session.scalar(select(TopicRelation)) is None
        assert session.scalar(select(TopicRelationProposal)).status == "rejected"
        correction = session.get(TopicAnalysisJob, identifier).result["corrections"][0]
        assert correction["previous_review_note"] == original
        assert correction["actor"] == actor


@pytest.mark.integration
def test_semantic_failure_keeps_recovered_citations_for_next_attempt(
    admin_client, database, catalog, monkeypatch
):
    identifier = queue(admin_client, catalog[0])
    monkeypatch.setattr(
        topic_analysis_tasks,
        "verify_citations",
        lambda values: evidence(values, reason="transport_error"),
    )
    execute(monkeypatch, identifier, [suggestion(catalog)])
    enable(monkeypatch, relationships=True)
    schedule_verification(database)
    calls = []

    def fetch(values):
        calls.append(values)
        return evidence(values)

    def timeout(*args, **kwargs):
        raise AnalysisError("codex_timeout")

    monkeypatch.setattr(tasks, "verify_citations", fetch)
    monkeypatch.setattr(
        tasks,
        "CodexClient",
        lambda _: SimpleNamespace(complete=timeout, usage={"total_tokens": 42}),
    )
    tasks.verify_research(str(identifier))
    with database.begin() as session:
        task = session.get(ResearchVerificationJob, identifier)
        assert task.status == "queued" and task.error == "codex_timeout"
        assert task.usage["total_tokens"] == 42
        task.available_at = utcnow()
        job = session.get(TopicAnalysisJob, identifier)
        assert all(
            check["status"] == "verified"
            for check in job.result["evidence_verification"]["checks"].values()
        )
    verify(monkeypatch, identifier)
    assert len(calls) == 1
    with database() as session:
        assert session.scalar(select(TopicRelationProposal)).status == "approved"


@pytest.mark.integration
def test_concurrent_scheduler_admission_is_bounded(database, catalog, monkeypatch):
    enable(monkeypatch, relationships=True)
    with database.begin() as session:
        for relation in ("uses_language", "depends_on", "implements", "part_of", "related_to"):
            job_id = uuid.uuid4()
            session.add(
                TopicAnalysisJob(
                    id=job_id,
                    topic_id=catalog[0],
                    status="succeeded",
                    outcome="enriched",
                    finished_at=utcnow(),
                    input_hash="a" * 64,
                    input_snapshot={},
                    requested_by={},
                    prompt_version="fixture",
                )
            )
            session.flush()
            session.add(
                TopicRelationProposal(
                    job_id=job_id,
                    **suggestion(catalog, relation=relation),
                    topic_snapshot={},
                    related_topic_snapshot={},
                    created_by={},
                )
            )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_verification(database), range(2)))
    assert sum(results) == 4
    with database() as session:
        assert len(session.scalars(select(ResearchVerificationJob)).all()) == 4
