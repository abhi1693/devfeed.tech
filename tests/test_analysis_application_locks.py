"""Catalog writers can progress during ranking without allowing stale applications."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_core import analysis
from devfeed_core.db import get_engine
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow
from devfeed_core.topics import lock_topics
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session
from test_automation_integration import enable, seed

pytestmark = pytest.mark.integration


@pytest.fixture
def worker_article(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        source, article, topic = seed(session, mode="auto")
        job = analysis.request_analysis(session, article.id)
        state = SimpleNamespace(
            source_id=source.id,
            article_id=article.id,
            topic_id=topic.id,
            job_id=job.id,
            inferences=[],
        )

    def complete(*args):
        state.inferences.append(True)
        return {
            "outcome": "ready",
            "developer_relevance": "relevant",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "ai_summary": "A guide to routing in Angular applications.",
            "ai_title": None,
            "title_evidence": None,
            "page_kind": "article",
            "ai_description": None,
            "topics": [
                {
                    "topic_id": str(state.topic_id),
                    "role": "primary",
                    "relevance": 0.9,
                    "evidence": "Angular",
                }
            ],
            "tags": [],
            "reasons": [],
        }

    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    return state


@pytest.fixture
def unlocked_ranking(worker_article, monkeypatch):
    state = SimpleNamespace(calls=0, hook=None)
    connections, locked = defaultdict(int), defaultdict(bool)
    score = analysis._candidate_scores

    def checkout(*args):
        connections[get_ident()] += 1

    def checkin(*args):
        connections[get_ident()] -= 1

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement or "LOCK TABLE topics" in statement:
            locked[get_ident()] = True

    def released(session, transaction):
        if transaction.parent is None:
            locked[get_ident()] = False

    def ranking(*args):
        assert not locked[get_ident()], "Ranking cannot hold publication or catalog locks"
        assert connections[get_ident()] == 0, "Ranking cannot keep a database connection"
        state.calls += 1
        if state.hook:
            state.hook(state.calls)
        return score(*args)

    monkeypatch.setattr(analysis, "_candidate_scores", ranking)
    listeners = [
        (get_engine(), "checkout", checkout),
        (get_engine(), "checkin", checkin),
        (get_engine(), "before_cursor_execute", capture),
        (Session, "after_transaction_end", released),
    ]
    for target, name, callback in listeners:
        event.listen(target, name, callback)
    try:
        yield state
    finally:
        for target, name, callback in listeners:
            event.remove(target, name, callback)


def test_catalog_writer_commits_while_worker_ranking_is_paused(
    database, worker_article, unlocked_ranking
):
    paused, resume = Event(), Event()

    def pause(call):
        if call == 2:  # Application preparation follows the inference catalog ranking.
            paused.set()
            assert resume.wait(10), "The writer did not finish while ranking was paused"

    unlocked_ranking.hook = pause
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(analysis_tasks._analyze, worker_article.job_id)
        try:
            assert paused.wait(10), "Application preparation did not reach ranking"
            with database.begin() as writer:
                writer.execute(text("SET LOCAL lock_timeout='2s'"))
                lock_topics(writer)
                writer.execute(
                    text(
                        "UPDATE topics SET description='Catalog maintenance metadata' WHERE id=:id"
                    ),
                    {"id": worker_article.topic_id},
                )
        finally:
            resume.set()
        worker.result(timeout=15)
    with database() as session:
        assert session.get(Article, worker_article.article_id).publication_status == "published"
        assert session.get(ArticleAnalysisJob, worker_article.job_id).outcome == "applied"
    assert unlocked_ranking.calls == 3  # Revision changed, so prepare again outside locks.
    assert len(worker_article.inferences) == 1


@pytest.mark.parametrize(
    "change", ["topic", "tag", "content", "editorial", "source", "disabled_source", "lease"]
)
def test_changed_inputs_or_ownership_cannot_apply_a_stale_result(
    database, worker_article, unlocked_ranking, monkeypatch, change
):
    prepare = analysis_tasks._prepare_application
    changed = []

    def racing_prepare(*args):
        prepared = prepare(*args)
        if not changed:
            changed.append(True)
            with database.begin() as editor:
                if change == "topic":
                    editor.execute(
                        text("UPDATE topics SET name='Changed identity' WHERE id=:id"),
                        {"id": worker_article.topic_id},
                    )
                elif change == "tag":
                    editor.execute(
                        text(
                            "INSERT INTO tags(id,name,slug,aliases) "
                            "VALUES (gen_random_uuid(),'New label','new-label','{}')"
                        )
                    )
                elif change == "content":
                    editor.execute(
                        text(
                            "UPDATE articles SET summary=summary || ' New source evidence.' "
                            "WHERE id=:id"
                        ),
                        {"id": worker_article.article_id},
                    )
                elif change == "editorial":
                    editor.execute(
                        text(
                            "UPDATE articles SET editorial_revision=editorial_revision+1 "
                            "WHERE id=:id"
                        ),
                        {"id": worker_article.article_id},
                    )
                elif change == "source":
                    editor.execute(
                        text("UPDATE sources SET approval_status='rejected' WHERE id=:id"),
                        {"id": worker_article.source_id},
                    )
                elif change == "disabled_source":
                    editor.execute(
                        text("UPDATE sources SET enabled=false WHERE id=:id"),
                        {"id": worker_article.source_id},
                    )
                else:
                    editor.execute(
                        text("UPDATE article_analysis_jobs SET lease_token=:token WHERE id=:id"),
                        {"token": uuid4(), "id": worker_article.job_id},
                    )
        return prepared

    monkeypatch.setattr(analysis_tasks, "_prepare_application", racing_prepare)
    analysis_tasks._analyze(worker_article.job_id)
    with database() as session:
        article = session.get(Article, worker_article.article_id)
        job = session.get(ArticleAnalysisJob, worker_article.job_id)
        if change == "tag":
            assert article.publication_status == "published" and job.outcome == "applied"
        elif change == "disabled_source":
            assert article.publication_status == "unpublished" and job.outcome == "applied"
            assert "source_policy_manual" in job.result["publication_policy"]["reasons"]
        else:
            assert (
                article.publication_status == "unpublished"
                and not article.classification_provenance
            )
            assert job.outcome == (
                "unapproved" if change == "source" else None if change == "lease" else "superseded"
            )
        if change in {"topic", "content"}:
            jobs = session.scalars(select(ArticleAnalysisJob)).all()
            assert len(jobs) == 2
            replacement = next(item for item in jobs if item.id != job.id)
            assert replacement.status == "queued"
            if change == "content":
                assert replacement.input_snapshot["text"] == article.summary
            else:
                assert replacement.catalog_hash != job.catalog_hash
    assert len(worker_article.inferences) == 1


def test_continuously_changing_catalog_uses_bounded_dependency_retry(
    database, worker_article, unlocked_ranking, monkeypatch
):
    prepare = analysis_tasks._prepare_application

    def racing_prepare(*args):
        prepared = prepare(*args)
        with database.begin() as editor:
            editor.execute(text("UPDATE topics SET description=gen_random_uuid()::text"))
        return prepared

    monkeypatch.setattr(analysis_tasks, "_prepare_application", racing_prepare)
    analysis_tasks._analyze(worker_article.job_id)
    with database() as session:
        article = session.get(Article, worker_article.article_id)
        job = session.get(ArticleAnalysisJob, worker_article.job_id)
        assert article.publication_status == "unpublished" and not article.classification_provenance
        assert job.status == "queued" and job.error == "analysis_dependency_failure"
        assert job.available_at > utcnow() and job.attempts == 1
        assert not job.lease_token
    assert unlocked_ranking.calls == 4  # One inference ranking plus three preparations.
    assert len(worker_article.inferences) == 1
