"""Production-shaped regressions on disposable data; no production mutations."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator.scheduler import recover_jobs
from devfeed_core import analysis, article_automation
from devfeed_core.article_jobs import approved_sources
from devfeed_core.db import get_engine
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.models import ArticleAnalysisJob, TopicProposal, utcnow
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from tests.workers.test_automation_integration import seed

pytestmark = pytest.mark.integration


def test_session_renewal_cas_preserves_revocation_and_concurrent_renewal(database):
    from devfeed_core.config import get_settings
    from devfeed_user_api.auth import RENEW_SESSION
    from redis import Redis

    with Redis.from_url(get_settings().redis_url) as connection:
        key = "test:reader-session"
        connection.set(key, "old", ex=60)
        assert connection.eval(RENEW_SESSION, 1, key, "old", "new", 120) == b"new"
        assert 110 <= connection.ttl(key) <= 120
        assert connection.eval(RENEW_SESSION, 1, key, "old", "stale", 600) == b"new"
        assert connection.ttl(key) <= 120
        connection.delete(key)
        assert connection.eval(RENEW_SESSION, 1, key, "new", "revived", 120) is None
        assert not connection.exists(key)


def test_source_guards_share_approval_but_block_revocation(database):
    with database.begin() as session:
        source, article, _ = seed(session)
        source_id, article_id = source.id, article.id
    with database.begin() as first:
        assert approved_sources(first, article_id, lock=True) == [source_id]
        with database.begin() as second:
            second.execute(text("SET LOCAL lock_timeout = '300ms'"))
            assert approved_sources(second, article_id, lock=True) == [source_id]
            with pytest.raises(OperationalError) as failure, database.begin() as reviewer:
                reviewer.execute(text("SET LOCAL lock_timeout = '100ms'"))
                reviewer.execute(
                    text("UPDATE sources SET approval_status='rejected' WHERE id=:id"),
                    {"id": source_id},
                )
            assert failure.value.orig.sqlstate == "55P03"
    with database.begin() as reviewer:
        reviewer.execute(
            text("UPDATE sources SET approval_status='rejected' WHERE id=:id"), {"id": source_id}
        )
    with database() as session:
        assert approved_sources(session, article_id, lock=True) == []


def test_pending_topic_matching_projects_identities_and_normalizes_article_once(
    database, monkeypatch
):
    drafts = [
        {"name": "Unrelated", "slug": "unrelated", "description": "Angular " * 10000},
        {"name": "Other", "slug": "other", "aliases": ["Not Angular"], "keywords": []},
        {"name": "Framework", "slug": "framework", "aliases": ["Angular"], "keywords": []},
    ]
    with database.begin() as session:
        for i, draft in enumerate(drafts):
            session.add(
                TopicProposal(
                    batch_id=uuid.uuid4(),
                    slug=f"draft-{i}",
                    action="create",
                    origin="import",
                    source_name="Test",
                    proposed=draft,
                    created_by={},
                    status="pending",
                )
            )
    calls = []
    original = article_automation.candidate_evidence

    def evidence(snapshot):
        calls.append(snapshot)
        return original(snapshot)

    monkeypatch.setattr(article_automation, "candidate_evidence", evidence)
    with database() as session:
        for body in ("Angular routing", "Unknown routing"):
            snapshot = {"title": body, "text": body * 1000}
            expected = any(analysis.candidate_score(draft, snapshot) > 0 for draft in drafts)
            assert article_automation.pending_topic_matches(session, snapshot) is expected
    assert len(calls) == 2


def test_analysis_request_reuses_a_catalog_held_by_the_caller(database, monkeypatch):
    with database.begin() as session:
        _, article, _ = seed(session)
        taxonomy = analysis.catalog(session)
        monkeypatch.setattr(analysis, "catalog", lambda *_: pytest.fail("duplicate catalog read"))
        job = analysis.request_analysis(session, article.id, taxonomy=taxonomy)
        assert job.catalog_hash == analysis.snapshot_hash(
            analysis.analysis_candidates(taxonomy, job.input_snapshot)
        )


@pytest.mark.parametrize("kind", ["analysis"])
def test_dispatch_and_recovery_preserve_bodies_without_loading_them(database, kind):
    body = {"private_evidence": "evidence " * 8000}
    with database.begin() as session:
        _, article, _ = seed(session)
        job = ArticleAnalysisJob(
            article_id=article.id, input_snapshot=body, catalog_snapshot=body, result=body
        )
        session.add(job)
        session.flush()
        identifier = job.id
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT"):
            statements.append(statement)

    queue = SimpleNamespace(enqueue=lambda *a, **kw: SimpleNamespace(id="test-delivery"))
    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        assert dispatch_jobs(database, queue, 1, utcnow(), kind=kind) == 1
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    model = JOB_DEFINITIONS[kind].model
    with database.begin() as session:
        job = session.get(model, identifier)
        job.status, job.attempts = "running", 1
        job.lease_until = utcnow() - timedelta(minutes=1)
        job.lease_token = uuid.uuid4()
    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        assert recover_jobs(database, 10, utcnow(), kind=kind) == 1
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    metadata_reads = [statement for statement in statements if "jsonb_build_object(" in statement]
    # Dispatch and recovery still perform exactly their two indexed work reads.
    # Direct analytics may read narrow before/after metadata for each changed job.
    # Account for that bounded work while checking every projection for payloads.
    assert len(statements) - len(metadata_reads) == 2
    assert len(metadata_reads) <= 4
    for field in ("input_snapshot", "catalog_snapshot", "result", "payload", "requested_by"):
        assert all(f".{field}" not in statement for statement in statements)
    with database() as session:
        job = session.get(model, identifier)
        assert job.status == "queued" and job.lease_token is None
        assert job.result == body
