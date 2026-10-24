"""Scheduler publication keeps authoritative guards without ranking while holding locks."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.scheduler import recover_jobs
from devfeed_core import analysis, article_automation, publication_policy
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from tests.workers.test_automation_integration import enable, ready, seed
from tests.workers.test_full_app_automation import full

pytestmark = pytest.mark.integration


def test_publication_batch_ranks_once_per_article_before_locks(database, monkeypatch):
    full(monkeypatch, AUTOMATION_BATCH_SIZE=5)
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    with database.begin() as session:
        articles = [seed(session, name=f"Framework{index}") for index in range(5)]
        for _, article, topic in articles:
            ready(session, article, topic)
    locked = [False]
    connections = [0]
    ranks, evaluations, loads = [], [], []
    rank = analysis._candidate_scores
    evaluate = publication_policy.evaluate_publication
    load = analysis._read_catalog

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement or "LOCK TABLE topics" in statement:
            locked[0] = True

    def released(session, transaction):
        if transaction.parent is None:
            locked[0] = False

    def checkout(*args):
        connections[0] += 1

    def checkin(*args):
        connections[0] -= 1

    def ranking(*args):
        assert not locked[0], "Full-catalog ranking must precede row and taxonomy locks"
        assert connections[0] == 0, "Ranking must release the read transaction"
        ranks.append(True)
        return rank(*args)

    def evaluation(*args, **kwargs):
        evaluations.append(True)
        return evaluate(*args, **kwargs)

    def loading(*args):
        loads.append(True)
        return load(*args)

    monkeypatch.setattr(analysis, "_candidate_scores", ranking)
    monkeypatch.setattr(analysis, "_read_catalog", loading)
    monkeypatch.setattr(publication_policy, "evaluate_publication", evaluation)
    monkeypatch.setattr(article_automation, "evaluate_publication", evaluation)
    event.listen(get_engine(), "before_cursor_execute", capture)
    event.listen(get_engine(), "checkout", checkout)
    event.listen(get_engine(), "checkin", checkin)
    event.listen(Session, "after_transaction_end", released)
    try:
        counts = article_automation.schedule_article_automation(database)
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
        event.remove(get_engine(), "checkout", checkout)
        event.remove(get_engine(), "checkin", checkin)
        event.remove(Session, "after_transaction_end", released)
    assert counts["articles_published"] == 5
    assert len(ranks) == len(evaluations) == 5
    assert len(loads) == 1


@pytest.mark.parametrize("changed", ["catalog", "content", "editorial", "source"])
def test_change_between_preparation_and_locking_never_applies_stale_result(
    database, monkeypatch, changed
):
    full(monkeypatch)
    with database.begin() as session:
        source, article, topic = seed(session)
        ready(session, article, topic)
        identifier, source_id, topic_id = article.id, source.id, topic.id
    prepare = article_automation._prepare_article_candidates

    def racing_prepare(*args):
        result = prepare(*args)
        with database.begin() as editor:
            if changed == "catalog":
                editor.execute(
                    text("UPDATE topics SET name='Changed' WHERE id=:id"), {"id": topic_id}
                )
            elif changed == "content":
                editor.execute(
                    text("UPDATE articles SET summary='Changed article text' WHERE id=:id"),
                    {"id": identifier},
                )
            elif changed == "editorial":
                editor.execute(
                    text(
                        "UPDATE articles SET editorial_revision=editorial_revision+1 WHERE id=:id"
                    ),
                    {"id": identifier},
                )
            else:
                editor.execute(
                    text("UPDATE sources SET enabled=false WHERE id=:id"), {"id": source_id}
                )
        return result

    monkeypatch.setattr(article_automation, "_prepare_article_candidates", racing_prepare)
    counts = article_automation.schedule_article_automation(database)
    assert counts["articles_published"] == counts["articles_rejected"] == 0
    with database() as session:
        article = session.get(Article, identifier)
        assert article.review_status == "pending" and article.publication_status == "unpublished"
        if changed in {"catalog", "content"}:
            assert article.automation_next_check_at <= utcnow()
            jobs = session.scalars(
                select(ArticleAnalysisJob).order_by(
                    ArticleAnalysisJob.created_at, ArticleAnalysisJob.id
                )
            ).all()
            assert jobs[0].status == "succeeded" and jobs[0].outcome == "applied"
            if changed == "content":
                assert len(jobs) == 2
                assert jobs[1].status == "queued"
                assert jobs[1].input_hash == analysis.snapshot_hash(
                    analysis.source_snapshot(article, None)
                )
                assert jobs[1].input_hash != jobs[0].input_hash
                assert not jobs[1].result
            else:
                assert len(jobs) == 1


@pytest.mark.parametrize("status", ["queued", "running"])
def test_repeated_active_checks_preserve_work_without_hydrating_analysis_evidence(
    database, monkeypatch, status
):
    full(monkeypatch, AUTOMATION_BATCH_SIZE=2)
    now = utcnow()
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        job = ready(session, article, topic)
        article.automation_started_at = now - timedelta(hours=25)
        article.automation_next_check_at = now - timedelta(seconds=1)
        job.status, job.attempts = status, 2
        job.finished_at = None
        job.available_at = now + timedelta(hours=1)
        job.lease_token = uuid.uuid4() if status == "running" else None
        job.lease_until = now + timedelta(minutes=20) if status == "running" else None
        job.input_snapshot = {"large_input": "publisher evidence " * 10000}
        job.catalog_snapshot = {"large_catalog": "retained catalog evidence " * 10000}
        job.result = {"large_result": "retained analysis evidence " * 10000}
        job.usage = {"large_usage": "retained specialist evidence " * 10000}
        identifier, job_id = article.id, job.id
        before = (
            job.status,
            job.attempts,
            job.available_at,
            job.lease_token,
            job.lease_until,
        )
    selects = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "FROM article_analysis_jobs" in statement:
            selects.append(statement.split("FROM", 1)[0])

    def unexpected_catalog(*_):
        pytest.fail("An active-job check must not hydrate the taxonomy")

    monkeypatch.setattr(analysis, "_read_catalog", unexpected_catalog)
    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        for index in range(3):
            tick_at = now + index * article_automation.CHECK_INTERVAL
            monkeypatch.setattr(article_automation, "utcnow", lambda tick_at=tick_at: tick_at)
            counts = article_automation.schedule_article_automation(database)
            assert counts["articles_checked"] == 1
            assert counts["articles_published"] == counts["articles_rejected"] == 0
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    # Preparation status, locked metadata, one readiness fact load per tick.
    assert len(selects) == 9
    for statement in selects:
        for field in ("input_snapshot", "catalog_snapshot", "result", "usage"):
            assert f"article_analysis_jobs.{field}" not in statement
    with database() as session:
        article, job = session.get(Article, identifier), session.get(ArticleAnalysisJob, job_id)
        assert article.automation_next_check_at == tick_at + article_automation.CHECK_INTERVAL
        assert article.automation_started_at == now - timedelta(hours=25)
        assert (
            job.status,
            job.attempts,
            job.available_at,
            job.lease_token,
            job.lease_until,
        ) == before
        assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1


def test_analysis_completing_after_preparation_remains_due_and_publishes_on_the_next_tick(
    database, monkeypatch
):
    full(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        job = ready(session, article, topic)
        job.status, job.finished_at = "running", None
        identifier, job_id = article.id, job.id
    prepare = article_automation._prepare_article_candidates

    def completing_prepare(*args):
        prepared = prepare(*args)
        assert prepared is None
        with database.begin() as worker:
            job = worker.get(ArticleAnalysisJob, job_id)
            finish_job(job, "applied", utcnow())
        return prepared

    monkeypatch.setattr(article_automation, "_prepare_article_candidates", completing_prepare)
    counts = article_automation.schedule_article_automation(database)
    assert counts["articles_checked"] == 1 and counts["articles_published"] == 0
    with database() as session:
        assert session.get(Article, identifier).automation_next_check_at <= utcnow()
        assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1
    monkeypatch.setattr(article_automation, "_prepare_article_candidates", prepare)
    assert article_automation.schedule_article_automation(database)["articles_published"] == 1


@pytest.mark.parametrize("step_attempts,status", [(1, "queued"), (3, "failed")])
def test_no_outcome_maintenance_keeps_expired_specialists_recoverable_without_duplicate_jobs(
    database, monkeypatch, step_attempts, status
):
    enable(monkeypatch)
    now = utcnow()
    with database.begin() as session:
        _, article, topic = seed(session)
        job = ready(session, article, topic)
        job.status, job.finished_at, job.attempts = "running", None, 99
        job.lease_token, job.lease_until = uuid.uuid4(), now - timedelta(seconds=1)
        job.usage = {
            "active_step": "overview",
            "article_steps": {
                "developer_relevance": {"status": "succeeded", "output": {"relevant": True}},
                "overview": {"status": "running", "attempts": step_attempts},
            },
        }
        identifier, job_id, token = article.id, job.id, job.lease_token

    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    with database() as session:
        job = session.get(ArticleAnalysisJob, job_id)
        assert job.status == "running" and job.lease_token == token

    assert recover_jobs(database, 10, now) == 1
    with database.begin() as session:
        article = session.get(Article, identifier)
        article.automation_next_check_at = now - timedelta(seconds=1)
        job = session.get(ArticleAnalysisJob, job_id)
        assert job.status == status
        assert job.lease_token is None and job.lease_until is None
        assert job.steps["overview"]["status"] == ("pending" if status == "queued" else "failed")
        assert job.steps["developer_relevance"]["output"] == {"relevant": True}
        available_at = job.available_at

    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    with database() as session:
        jobs = session.scalars(select(ArticleAnalysisJob)).all()
        assert len(jobs) == 1
        assert jobs[0].id == job_id and jobs[0].status == status
        assert jobs[0].available_at == available_at and jobs[0].attempts == 99
