"""Production-shaped regressions on disposable data; no production mutations."""

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from devfeed_aggregator.scheduler import recover_jobs
from devfeed_core import analysis, article_automation
from devfeed_core.article_jobs import approved_sources
from devfeed_core.db import get_engine
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.models import ArticleAnalysisJob, NotificationDelivery, TopicProposal, utcnow
from devfeed_notifications.delivery import recover_notifications
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from test_automation_integration import seed

pytestmark = pytest.mark.integration


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


@pytest.mark.parametrize("kind", ["analysis", "notifications"])
def test_dispatch_and_recovery_preserve_bodies_without_loading_them(database, kind):
    body = {"private_evidence": "evidence " * 8000}
    with database.begin() as session:
        if kind == "analysis":
            _, article, _ = seed(session)
            job = ArticleAnalysisJob(
                article_id=article.id, input_snapshot=body, catalog_snapshot=body, result=body
            )
        else:
            job = NotificationDelivery(
                event_key="test", dedup_key="test", audience="admin", category="test", payload=body
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
        if kind == "notifications":
            assert recover_notifications(database, 10, utcnow()) == 1
        else:
            assert recover_jobs(database, 10, utcnow(), kind=kind) == 1
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    assert len(statements) == 2
    for field in ("input_snapshot", "catalog_snapshot", "result", "payload", "requested_by"):
        assert all(f".{field}" not in statement for statement in statements)
    with database() as session:
        job = session.get(model, identifier)
        assert job.status == "queued" and job.lease_token is None
        assert (job.result if kind == "analysis" else job.payload) == body


def test_recovery_index_skips_large_completed_history(database):
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO notification_deliveries
                (id,event_key,dedup_key,audience,category,payload,status,attempts,
                 available_at,created_at,finished_at)
            SELECT gen_random_uuid(), 'history-'||i, md5(i::text), 'admin', 'test',
                   jsonb_build_object('body',repeat('historical notification ',100)),
                   'succeeded', 1, now(), now(), now()
            FROM generate_series(1,50000) i
        """)
        )
        connection.execute(text("ANALYZE notification_deliveries"))
    sql = str(
        select(NotificationDelivery)
        .options(*JOB_DEFINITIONS["notifications"].metadata_options())
        .where(
            NotificationDelivery.status == "running", NotificationDelivery.lease_until < utcnow()
        )
        .order_by(NotificationDelivery.lease_until)
        .limit(50)
        .with_for_update(skip_locked=True)
        .compile(engine, compile_kwargs={"literal_binds": True})
    )
    with engine.connect() as connection:
        # Remove the new index only inside a rolled-back disposable transaction
        # to measure the previous scan against exactly the same 50,000 rows.
        connection.execute(text("DROP INDEX ix_notification_deliveries_running_lease"))
        before = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + sql
        ).scalar_one()[0]
        connection.rollback()
        after = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + sql
        ).scalar_one()[0]
    assert "ix_notification_deliveries_running_lease" in json.dumps(after)
    assert after["Plan"]["Actual Rows"] == before["Plan"]["Actual Rows"] == 0
    assert after["Plan"]["Shared Hit Blocks"] + after["Plan"]["Shared Read Blocks"] < 20
    report = {
        "rows": 50000,
        "before_ms": before["Execution Time"],
        "after_ms": after["Execution Time"],
        "before_buffers": before["Plan"]["Shared Hit Blocks"]
        + before["Plan"]["Shared Read Blocks"],
        "after_buffers": after["Plan"]["Shared Hit Blocks"] + after["Plan"]["Shared Read Blocks"],
    }
    if target := os.environ.get("DEVFEED_HOTSPOT_PROFILE_REPORT"):
        Path(target).write_text(json.dumps(report, indent=2) + "\n")
