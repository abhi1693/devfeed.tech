"""Publication transitions and durable analysis writes on disposable services."""

import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event

import pytest
from devfeed_admin_api.topics import AdminTopicWrite, topic_update
from devfeed_core import analysis, editorial
from devfeed_core.cache import close_cache
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
    utcnow,
)
from devfeed_core.urls import fingerprint
from sqlalchemy import event, select, text

pytestmark = pytest.mark.integration


def setup_article(database):
    with database.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://example.com/rss",
            source_type="publisher",
            approval_status="approved",
            enabled=False,
        )
        topic = Topic(name="Angular", slug="angular", kind="framework", status="active")
        article = Article(
            canonical_url="https://example.com/angular",
            url_hash=fingerprint("https://example.com/angular"),
            title="Angular routing",
            summary=(
                "Angular routing helps developers build navigation "
                "and organize their JavaScript applications."
            ),
            language="en",
        )
        session.add_all([source, topic, article])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="angular",
                original_url=article.canonical_url,
            )
        )
        session.flush()
        return article.id, topic.id


@pytest.mark.parametrize("previous_status", ["succeeded", "failed"])
def test_forced_analysis_backfill_creates_new_jobs_without_rewriting_history(
    database, previous_status
):
    article_id, _ = setup_article(database)
    with database.begin() as session:
        original = analysis.request_analysis(session, article_id)
        original.status, original.attempts = previous_status, 3
        original.finished_at = utcnow()
        original.error = "codex_timeout" if previous_status == "failed" else None
        original.result = {"retained": "original result"}
        original_id, digest = original.id, original.input_hash
    with database.begin() as session:
        jobs, scanned, _ = analysis.backfill_analyses(session, 100)
        assert scanned == 1 and jobs == []
    with database.begin() as session:
        jobs, scanned, _ = analysis.backfill_analyses(session, 100, force=True)
        assert scanned == 1 and len(jobs) == 1
        assert jobs[0].id != original_id and jobs[0].input_hash == digest
        assert jobs[0].status == "queued" and jobs[0].attempts == 0
        new_id = jobs[0].id
    with database.begin() as session:
        # A repeated forced batch must not steal or duplicate the active job.
        jobs, scanned, _ = analysis.backfill_analyses(session, 100, force=True)
        assert scanned == 0 and jobs == []
        rows = session.scalars(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == article_id)
        ).all()
        assert {job.id for job in rows} == {original_id, new_id}
        original = session.get(ArticleAnalysisJob, original_id)
        assert original.status == previous_status and original.attempts == 3
        assert original.result == {"retained": "original result"}


@pytest.mark.parametrize("excluded", ["rejected", "approved", "published", "source", "sparse"])
def test_forced_backfill_preserves_editorial_source_and_evidence_requirements(database, excluded):
    article_id, _ = setup_article(database)
    with database.begin() as session:
        article = session.get(Article, article_id)
        if excluded in {"rejected", "approved"}:
            article.review_status = excluded
        elif excluded == "published":
            article.review_status = "approved"
            article.publication_status = "published"
        elif excluded == "source":
            session.scalar(select(Source)).approval_status = "pending"
        else:
            article.summary = ""
    with database.begin() as session:
        jobs, _, _ = analysis.backfill_analyses(session, 100, force=True)
        assert jobs == []
        assert session.scalar(select(ArticleAnalysisJob)) is None


def test_analysis_then_explicit_publication_and_cached_unpublish(database, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    close_cache()
    try:
        article_id, topic_id = setup_article(database)
        path = f"/v1/articles/{article_id}"
        assert client.get(path).status_code == 404
        with database.begin() as session:
            job = analysis.request_analysis(session, article_id)
            job.model = "test-model"
            original = session.get(Article, article_id).summary
            result = analysis.AnalysisResult(
                outcome="ready",
                developer_relevance="relevant",
                language="en",
                content_type="tutorial",
                content_format="article",
                ai_summary="AI generated explanation of Angular routing.",
                ai_description=None,
                topics=[
                    analysis.TopicSelection(
                        topic_id=topic_id,
                        role="primary",
                        relevance=0.95,
                        evidence="Angular routing",
                    )
                ],
                tags=[],
                proposed_topics=[],
                reasons=[],
            )
            analysis.validate_evidence(result, job.input_snapshot, analysis.catalog(session))
            analysis.apply_analysis(session, session.get(Article, article_id), job, result)
            assert job.outcome == "applied"
        assert client.get(path).status_code == 404
        with database.begin() as session:
            editorial.decide_article(
                session, article_id, editorial.EditorialDecision(action="approve")
            )
        assert client.get(path).status_code == 404
        with database.begin() as session:
            editorial.decide_article(
                session, article_id, editorial.EditorialDecision(action="publish")
            )
        payload = client.get(path).json()
        assert payload["summary"] == original and payload["ai_summary"] != original
        assert client.get(path).headers["x-cache"] == "HIT"
        assert client.get("/v1/feed?topic=angular").json()["items"][0]["id"] == str(article_id)
        with database.begin() as session:
            editorial.decide_article(
                session, article_id, editorial.EditorialDecision(action="unpublish")
            )
        assert client.get(path).status_code == 404
        assert client.get("/v1/feed?topic=angular").json()["items"] == []
    finally:
        close_cache()


def test_content_changes_and_manual_decisions_discard_inflight_result(database):
    article_id, _ = setup_article(database)
    with database.begin() as session:
        job = analysis.request_analysis(session, article_id)
        job_id = job.id
    with database.begin() as session:
        editorial.decide_article(
            session,
            article_id,
            editorial.EditorialDecision(action="reject", note="Operator decision"),
        )
    with database.begin() as session:
        article = session.get(Article, article_id)
        job = session.get(ArticleAnalysisJob, job_id)
        result = analysis.AnalysisResult(
            outcome="insufficient_evidence",
            developer_relevance="uncertain",
            language=None,
            content_type=None,
            content_format=None,
            ai_summary=None,
            ai_description=None,
            topics=[],
            tags=[],
            proposed_topics=[],
            reasons=[],
        )
        analysis.apply_analysis(session, article, job, result)
        assert job.outcome == "superseded"
        assert article.review_status == "rejected" and article.publication_status == "unpublished"
    with database() as session:
        assert (
            session.scalar(
                select(ArticleAnalysisJob.outcome).where(ArticleAnalysisJob.id == job_id)
            )
            == "superseded"
        )


def test_analysis_assignments_and_topic_slug_edits_use_consistent_lock_order(database):
    article_id, topic_id = setup_article(database)
    result = analysis.AnalysisResult(
        outcome="ready",
        developer_relevance="relevant",
        language="en",
        content_type="tutorial",
        content_format="article",
        ai_summary=None,
        ai_description=None,
        tags=[],
        reasons=[],
        topics=[
            analysis.TopicSelection(
                topic_id=topic_id, role="primary", relevance=1, evidence="Angular routing"
            )
        ],
        proposed_topics=[],
    )
    with database.begin() as session:
        job = analysis.request_analysis(session, article_id)
        job.result = result.model_dump(mode="json")
        job_id = job.id
    inserted = Event()
    editor_pid = Queue()

    def analyze():
        with database.begin() as session:
            connection = session.connection()
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            analysis_pid = connection.scalar(text("SELECT pg_backend_pid()"))

            def after_assignment(conn, cursor, statement, parameters, context, executemany):
                if not statement.startswith("INSERT INTO article_topics"):
                    return
                # Hold the real FK locks while the editor attempts its table lock.
                inserted.set()
                pid = editor_pid.get(timeout=8)
                with get_engine().connect() as observer:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        if observer.scalar(
                            text("SELECT :analysis_pid = ANY(pg_blocking_pids(:editor_pid))"),
                            {"analysis_pid": analysis_pid, "editor_pid": pid},
                        ):
                            break
                        time.sleep(0.01)
                    else:
                        pytest.fail("Editor never reached the conflicting lock")

            event.listen(connection, "after_cursor_execute", after_assignment)
            try:
                analysis.apply_analysis(
                    session,
                    session.get(Article, article_id),
                    session.get(ArticleAnalysisJob, job_id),
                    result,
                )
            finally:
                event.remove(connection, "after_cursor_execute", after_assignment)

    def edit():
        assert inserted.wait(timeout=8)
        with database() as session:
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            editor_pid.put(session.scalar(text("SELECT pg_backend_pid()")))
            topic_update(
                topic_id,
                AdminTopicWrite(
                    name="Angular", slug="angular-router", kind="framework", status="active"
                ),
                session,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        analysis_result, edit_result = pool.submit(analyze), pool.submit(edit)
        analysis_result.result(timeout=20)
        edit_result.result(timeout=20)
    with database() as session:
        assert session.get(Topic, topic_id).slug == "angular-router"
        assert session.get(ArticleTopic, (article_id, topic_id)).role == "primary"
        assert session.get(ArticleAnalysisJob, job_id).outcome == "applied"
