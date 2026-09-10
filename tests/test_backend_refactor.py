"""Regression contracts for shared infrastructure and bounded monitoring reads."""

import json
import uuid
from types import SimpleNamespace

import pytest
from devfeed_admin_api.jobs import job_view
from devfeed_admin_api.workers import queue_snapshot, read_workers
from devfeed_core.config import get_settings
from devfeed_core.models import (
    IngestionJob,
    ResearchVerificationJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    utcnow,
)
from devfeed_core.services import OperationConflict
from devfeed_core.topics import TopicWrite, save_topic
from devfeed_core.verification_results import read_topic_verdict
from sqlalchemy import event
from test_admin_workers import TelemetryStore


@pytest.mark.integration
@pytest.mark.parametrize("population", [1, 20])
def test_worker_queries_are_bounded_by_models_not_worker_population(database, population):
    store = TelemetryStore()
    with database.begin() as session:
        for i in range(population):
            proposal = TopicProposal(
                batch_id=uuid.uuid4(),
                slug=f"subject-{i}",
                action="create",
                origin="import",
                source_name="test",
                proposed={"name": f"Subject {i}"},
                created_by={},
            )
            session.add(proposal)
            session.flush()
            job = TopicAnalysisJob(
                proposal_id=proposal.id,
                status="succeeded",
                outcome="enriched",
                input_hash="0" * 64,
                input_snapshot={},
                requested_by={},
                prompt_version="test",
            )
            session.add(job)
            session.flush()
            session.add(ResearchVerificationJob(id=job.id, relationships=False, status="running"))
            rq_id = f"rq-{i}"
            store.worker(f"ai-{i}", current_job=rq_id.encode())
            store.hashes["rq:job:" + rq_id] = {
                "data": json.dumps(
                    [
                        "devfeed_aggregator.research_verification_tasks.verify_research",
                        None,
                        [str(job.id)],
                        {},
                    ]
                ).encode(),
            }
    statements = []
    with database() as session:
        engine = session.get_bind()

        def record(_conn, _cursor, sql, *_args):
            statements.append(sql)

        event.listen(engine, "before_cursor_execute", record)
        try:
            workers = read_workers(store, session, utcnow())
            queues = queue_snapshot(store, session, workers)
        finally:
            event.remove(engine, "before_cursor_execute", record)
    assert len(workers) == population
    assert {w.current_job.target_name for w in workers} == {
        f"Subject {i}" for i in range(population)
    }
    assert all(w.current_job.status == "running" for w in workers)
    assert next(q for q in queues if q.name == "analysis").running == population
    assert len(statements) == 4  # Verification + parent + subject + queue aggregates.


def test_new_database_column_does_not_silently_enter_admin_response():
    values = {
        "id": uuid.uuid4(),
        "source_id": uuid.uuid4(),
        "status": "queued",
        "attempts": 0,
        "created_at": utcnow(),
        "available_at": utcnow(),
        "finished_at": None,
        "dispatched_at": None,
        "http_status": None,
        "entries_seen": 0,
        "articles_created": 0,
        "entries_skipped": 0,
        "error": None,
        "private_credential": "must remain private",
    }
    job = SimpleNamespace(
        **values,
        __table__=SimpleNamespace(
            columns=[
                *IngestionJob.__table__.columns,
                SimpleNamespace(name="private_credential"),
            ]
        ),
    )
    response = job_view(job, "ingestion").model_dump(mode="json")
    assert response["details"]["entries_seen"] == 0
    assert "private_credential" not in response["details"]
    assert "must remain private" not in json.dumps(response)


@pytest.mark.parametrize(
    "value,state",
    [
        ({}, "missing"),
        ({"check": None}, "invalid"),
        ({"check": {"verdict": "supported"}}, "supported"),
        ({"check": {"verdict": "unsupported"}}, "unsupported"),
        ({"check": {"verdict": "supported", "relevance": {"verdict": "uncertain"}}}, "uncertain"),
    ],
)
def test_persisted_verdict_states_remain_distinct(value, state):
    assert read_topic_verdict(value).state == state


@pytest.mark.integration
def test_indexed_topic_conflicts_preserve_rejected_unicode_and_shared_aliases(database):
    with database.begin() as session:
        session.add_all(
            [
                Topic(name="Straße", slug="street", kind="concept", status="rejected", aliases=[]),
                Topic(name="Delivery", slug="delivery", kind="concept", aliases=["CD"]),
            ]
        )
    with database.begin() as session, pytest.raises(OperationConflict, match="already identifies"):
        save_topic(session, TopicWrite(name="STRASSE", slug="another", kind="concept"))
    with database.begin() as session:
        topic = save_topic(
            session,
            TopicWrite(
                name="Deployment",
                slug="deployment",
                aliases=["CD"],
                kind="concept",
            ),
        )
        assert topic.name == "Deployment"


def test_verification_evaluation_uses_injected_evidence_without_database_io():
    from devfeed_aggregator.research_verification_tasks import (
        ModelAttempt,
        ResearchVerificationService,
        VerificationContext,
    )
    from devfeed_core.research_evidence import VERIFICATION_VERSION, citation_key

    now = utcnow()
    citation = ("https://example.com/reference", "An independently checked quote")
    calls = []

    def checker(citations):
        calls.append(citations)
        return {
            "version": VERIFICATION_VERSION,
            "checks": {
                citation_key(*citation): {"status": "verified"},
            },
        }

    service = ResearchVerificationService(
        get_settings(),
        SimpleNamespace(begin=lambda: pytest.fail("SQL during evaluation")),
        lambda _: pytest.fail("No semantic review requested"),
        checker,
        lambda: now,
    )
    context = VerificationContext(uuid.uuid4(), uuid.uuid4(), 1, [citation], [], None, {}, {}, {})
    outcome = service.evaluate(context, ModelAttempt(0))
    assert outcome.reason is None and outcome.citations_retried == 1
    assert calls == [[citation]]
