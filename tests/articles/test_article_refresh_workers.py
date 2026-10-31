"""Private maintenance runs through actual worker admission, checkpoints and promotion."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event

import pytest
from devfeed_aggregator import article_tasks
from devfeed_core.article_jobs import claim_article
from devfeed_core.article_processing_retry import request_processing_retry
from devfeed_core.article_refresh import review_refresh
from devfeed_core.article_refresh_processing import reconcile_refreshes, resume_refresh
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.inference_errors import AnalysisError
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleRefresh,
    FeedNotificationEvent,
    Source,
    utcnow,
)
from sqlalchemy import event, func, literal, select, text
from sqlalchemy.dialects import postgresql

from tests.articles.test_article_enrichment_integration import PAGE
from tests.support.article_maintenance_workers import complete_private_analysis
from tests.support.article_refresh import published_retry_case as published_retry_case

pytestmark = pytest.mark.integration


def test_private_analysis_finishes_atomic_publication_without_identity_or_notification_reset(
    database,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "analysis")
    refresh_id, job_id, calls = complete_private_analysis(database, monkeypatch, case.article_id)
    assert len(calls) >= 7
    with database() as session:
        owner, article = (
            session.get(ArticleRefresh, refresh_id),
            session.get(Article, case.article_id),
        )
        job = session.get(ArticleAnalysisJob, job_id)
        assert job.status == "succeeded" and job.outcome == "applied"
        assert owner.status == "promoted"
        assert article.ai_summary == "Inspect Python tasks with a complete private replacement."
        assert article.publication_status == "published" and article.review_status == "approved"
        assert article.slug == case.slug and article.published_to_feed_at == case.published_at
        assert article.editorial_revision == owner.base_revision + 1
        assert session.scalar(select(func.count()).select_from(FeedNotificationEvent)) == 1


def test_private_enrichment_keeps_live_body_until_its_analysis_completes(
    database,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "enrichment")
        owner = session.scalar(select(ArticleRefresh))
        refresh_id, job_id = owner.id, owner.enrichment_job_id
    monkeypatch.setattr(article_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        article_tasks,
        "fetch_article_page",
        lambda url: FetchResult(
            200,
            PAGE.replace(b'<meta property="og:image" content="/cover.jpg">', b""),
            url,
        ),
    )
    article_tasks.enrich_article(str(job_id))
    with database() as session:
        owner, article = (
            session.get(ArticleRefresh, refresh_id),
            session.get(Article, case.article_id),
        )
        assert owner.payload["content"]["text"]
        assert article.title == case.body["title"] and article.ai_summary == case.body["ai_summary"]
        assert session.get(ArticleContent, case.article_id) is None
        assert session.get(ArticleEnrichmentJob, job_id).status == "succeeded"
        assert session.get(ArticleAnalysisJob, owner.analysis_job_id).refresh_id == owner.id
    complete_private_analysis(database, monkeypatch, case.article_id)
    with database() as session:
        assert session.get(ArticleRefresh, refresh_id).status == "promoted"
        assert (
            "reliable applications with Python" in session.get(ArticleContent, case.article_id).text
        )


@pytest.mark.parametrize("withdrawal", ["unpublish", "reject", "source", "revision"])
def test_private_specialists_cannot_restore_withdrawn_authority(
    database,
    published_retry_case,
    monkeypatch,
    withdrawal,
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "analysis")

    def withdraw(call):
        if call != 1:
            return
        with database.begin() as session:
            article = session.get(Article, case.article_id)
            if withdrawal == "unpublish":
                article.publication_status = "unpublished"
            elif withdrawal == "reject":
                article.review_status, article.publication_status = "rejected", "unpublished"
            elif withdrawal == "revision":
                article.editorial_revision += 1
            else:
                session.get(Source, article.origins[0].source_id).approval_status = "rejected"

    refresh_id, job_id, calls = complete_private_analysis(
        database,
        monkeypatch,
        case.article_id,
        before_complete=withdraw,
    )
    with database() as session:
        assert len(calls) == 1
        assert session.get(ArticleAnalysisJob, job_id).outcome in {"superseded", "unapproved"}
        assert session.get(Article, case.article_id).ai_summary == case.body["ai_summary"]
    assert resume_refresh(database, refresh_id) == "superseded"


def test_complete_private_replacement_waits_for_version_bound_manual_consent(
    database,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    monkeypatch.setattr(get_settings(), "full_automation", False)
    with database.begin() as session:
        case.request(session, "analysis")
    refresh_id, _, _ = complete_private_analysis(database, monkeypatch, case.article_id)
    with database.begin() as session:
        owner = session.get(ArticleRefresh, refresh_id)
        assert owner.status == "review"
        article = session.get(Article, case.article_id)
        assert article.ai_summary == case.body["ai_summary"]
        review_refresh(
            session,
            case.article_id,
            refresh_id,
            owner.version,
            action="approve",
            actor={"subject": "editor"},
            note="Reviewed replacement",
        )
    with database() as session:
        assert session.get(ArticleRefresh, refresh_id).status == "promoted"
        assert session.get(Article, case.article_id).ai_summary != case.body["ai_summary"]


def test_due_claim_commits_before_resume_and_recovers_missed_final_callback(
    database,
    published_retry_case,
    monkeypatch,
):
    from devfeed_core import article_refresh_processing

    case = published_retry_case
    with database.begin() as session:
        case.request(session, "analysis")
    real = article_refresh_processing.resume_refresh
    monkeypatch.setattr(article_refresh_processing, "resume_refresh", lambda *args: "pending")
    refresh_id, _, _ = complete_private_analysis(database, monkeypatch, case.article_id)
    monkeypatch.setattr(article_refresh_processing, "resume_refresh", real)
    original_resume = article_refresh_processing.resume_refresh

    def observe(factory, identifier):
        with factory.begin() as session:
            session.execute(text("SET LOCAL lock_timeout='500ms'"))
            owner = session.scalar(
                select(ArticleRefresh).where(ArticleRefresh.id == identifier).with_for_update()
            )
            assert owner.next_check_at > utcnow()
        return original_resume(factory, identifier)

    monkeypatch.setattr(article_refresh_processing, "resume_refresh", observe)
    assert reconcile_refreshes(database, 100) == 1
    with database() as session:
        assert session.get(ArticleRefresh, refresh_id).status == "promoted"


@pytest.mark.parametrize("target", ["enrichment", "analysis"])
def test_private_claim_waits_for_source_before_taking_job_lock(
    database,
    published_retry_case,
    monkeypatch,
    target,
):
    from devfeed_aggregator.article_refresh_tasks import RefreshAnalysisService, analyze_refresh

    case = published_retry_case
    with database.begin() as session:
        case.request(session, target)
        owner = session.scalar(select(ArticleRefresh))
        refresh_id = owner.id
        identifier = owner.enrichment_job_id if target == "enrichment" else owner.analysis_job_id
    model = ArticleEnrichmentJob if target == "enrichment" else ArticleAnalysisJob
    started = Event()
    engine = database.kw["bind"]

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "FROM sources" in statement and "FOR SHARE" in statement:
            started.set()

    def claim():
        if target == "analysis":
            analyze_refresh(database, get_settings(), identifier, refresh_id)
            return True
        with database.begin() as session:
            session.execute(text("SET LOCAL statement_timeout='3s'"))
            return claim_article(session, identifier) is not None

    monkeypatch.setattr(RefreshAnalysisService, "run", lambda *_args: None)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with database.begin() as session:
                session.execute(text("SET LOCAL statement_timeout='3s'"))
                # The worker reaches Source SHARE before this opposing editor
                # can lock Article/job/owner; job-first workers would deadlock.
                session.scalar(select(Source).with_for_update())
                future = pool.submit(claim)
                assert started.wait(2)
                session.scalar(
                    select(Article).where(Article.id == case.article_id).with_for_update()
                )
                session.scalar(select(model).where(model.id == identifier).with_for_update())
                session.scalar(
                    select(ArticleRefresh).where(ArticleRefresh.id == refresh_id).with_for_update()
                )
            assert future.result(timeout=4)
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def test_private_specialists_do_not_hydrate_bodies_inside_subject_write_locks(
    database,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "analysis")
    engine, violations = database.kw["bind"], []

    def begin(connection):
        connection.info["maintenance_subject_locked"] = False

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "FROM sources" in statement and "FOR SHARE" in statement:
            connection.info["maintenance_subject_locked"] = True
        if not connection.info.get("maintenance_subject_locked") or not statement.startswith(
            "SELECT"
        ):
            return
        if (
            "article_refreshes.payload," in statement
            or "article_refreshes.payload \n" in statement
            or "article_contents.text" in statement
            or "article_analysis_jobs.input_snapshot" in statement
        ):
            violations.append(statement.split("FROM", 1)[0])

    event.listen(engine, "begin", begin)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        complete_private_analysis(database, monkeypatch, case.article_id)
        assert not violations
    finally:
        event.remove(engine, "begin", begin)
        event.remove(engine, "before_cursor_execute", observe)


def test_failed_private_extraction_never_falls_through_to_live_analysis_or_rejection(
    database,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "enrichment")
        owner = session.scalar(select(ArticleRefresh))
        refresh_id, identifier = owner.id, owner.enrichment_job_id

    def missing(url):
        raise FeedError("Gone", reason="http_error", status=410, retryable=False)

    monkeypatch.setattr(article_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(article_tasks, "fetch_article_page", missing)
    article_tasks.enrich_article(str(identifier))
    assert resume_refresh(database, refresh_id) == "pending"
    with database() as session:
        assert session.get(ArticleEnrichmentJob, identifier).status == "failed"
        article = session.get(Article, case.article_id)
        assert article.publication_status == "published" and article.review_status == "approved"
        assert article.ai_summary == case.body["ai_summary"]
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == 1


def test_private_failed_specialists_keep_independent_checkpoints_for_selected_manual_retry(
    database,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "analysis")

    def fail_at_fifth(call):
        if call == 5:
            raise AnalysisError("codex_timeout")

    refresh_id, identifier, first = complete_private_analysis(
        database,
        monkeypatch,
        case.article_id,
        before_complete=fail_at_fifth,
    )
    assert len(first) == 5
    with database.begin() as session:
        job = session.get(ArticleAnalysisJob, identifier)
        assert job.status == "queued"
        accepted = {
            name: value for name, value in job.steps.items() if value["status"] == "succeeded"
        }
        assert len(accepted) == 4
        job.available_at = utcnow() - timedelta(seconds=1)

    def fail_first_paid_call(call):
        raise AnalysisError("codex_timeout")

    for _ in range(2):
        with database.begin() as session:
            session.get(ArticleAnalysisJob, identifier).available_at = utcnow() - timedelta(
                seconds=1
            )
        complete_private_analysis(
            database, monkeypatch, case.article_id, before_complete=fail_first_paid_call
        )
    with database.begin() as session:
        job = session.get(ArticleAnalysisJob, identifier)
        assert job.status == "failed"
        article = session.get(Article, case.article_id)
        assert article.ai_summary == case.body["ai_summary"]
        request_processing_retry(
            session,
            article.id,
            target="analysis",
            expected_revision=article.editorial_revision,
            expected_job_id=identifier,
            actor={"subject": "editor"},
        )
        owner = session.get(ArticleRefresh, refresh_id)
        successor = session.get(ArticleAnalysisJob, owner.analysis_job_id)
        assert successor.id != identifier
        assert successor.steps == accepted
    _, successor_id, remaining = complete_private_analysis(database, monkeypatch, case.article_id)
    assert len(remaining) < 7
    with database() as session:
        assert session.get(ArticleRefresh, refresh_id).status == "promoted"
        assert session.get(ArticleAnalysisJob, identifier).status == "failed"
        assert session.get(ArticleAnalysisJob, successor_id).status == "succeeded"


def test_due_owner_page_uses_partial_index_above_one_hundred_thousand_terminal_owners(
    database,
    published_retry_case,
    record_testsuite_property,
):
    case = published_retry_case
    with database.begin() as session:
        case.request(session, "analysis")
        owner = session.scalar(select(ArticleRefresh))
        refresh_id = owner.id
        owner.next_check_at = utcnow() - timedelta(hours=1)
        session.flush()
        session.execute(
            text("""
            INSERT INTO article_refreshes
                (id, article_id, target, status, base_revision, base_hash, payload,
                 completed_at, requested_by, created_at)
            SELECT md5('terminal-maintenance-' || value::text)::uuid,
                   CAST(:article AS uuid), 'analysis', 'promoted', 0, repeat('b', 64), '{}',
                   now() - interval '100 days', '{}', now() - interval '100 days'
            FROM generate_series(1, 100000) value
        """),
            {"article": str(case.article_id)},
        )
        session.execute(text("ANALYZE article_refreshes"))
    actual = []

    def capture(state):
        statement = state.statement
        lock = getattr(statement, "_for_update_arg", None)
        if (
            lock is not None
            and lock.skip_locked
            and statement.column_descriptions[0]["entity"] is ArticleRefresh
        ):
            actual.append(statement)

    event.listen(database.class_, "do_orm_execute", capture)
    try:
        assert reconcile_refreshes(database, 100) == 1
    finally:
        event.remove(database.class_, "do_orm_execute", capture)
    assert len(actual) == 1
    compiled = actual[0].compile(
        dialect=postgresql.dialect(paramstyle="numeric"),
        compile_kwargs={"render_postcompile": True},
    )
    prepared_sql = re.sub(r":(\d+)", r"$\1", str(compiled))
    parameters = tuple(compiled.params[name] for name in compiled.positiontup)
    assert "article_refreshes.payload," not in prepared_sql
    assert 20 in parameters and any(isinstance(value, datetime) for value in parameters)
    with database.begin() as session:
        session.get(ArticleRefresh, refresh_id).next_check_at = utcnow() - timedelta(hours=1)
        session.flush()
        connection = session.connection()
        connection.exec_driver_sql("SET LOCAL plan_cache_mode=force_generic_plan")
        connection.exec_driver_sql("PREPARE maintenance_due_page AS " + prepared_sql)
        try:
            # EXECUTE takes SQL values, not protocol placeholders. PREPARE keeps
            # the actual runtime arguments and force_generic_plan ignores values.
            arguments = ", ".join(
                str(
                    literal(value).compile(
                        dialect=connection.dialect, compile_kwargs={"literal_binds": True}
                    )
                )
                for value in parameters
            )
            plan = connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE maintenance_due_page("
                + arguments
                + ")",
            ).scalar()[0]["Plan"]
            counts = connection.exec_driver_sql(
                "SELECT generic_plans,custom_plans FROM pg_prepared_statements "
                "WHERE name='maintenance_due_page'"
            ).one()
            assert tuple(counts) == (1, 0)
        finally:
            connection.exec_driver_sql("DEALLOCATE maintenance_due_page")
        record_testsuite_property("maintenance_actual_due_sql", prepared_sql)
        record_testsuite_property("maintenance_generic_due_plan", json.dumps(plan, sort_keys=True))

        def nodes(node):
            yield node
            for child in node.get("Plans", []):
                yield from nodes(child)

        indexes = [
            node
            for node in nodes(plan)
            if node.get("Index Name")
            in {
                "ix_article_refresh_due",
                "uq_article_refresh_active",
            }
        ]
        assert len(indexes) == 1 and indexes[0]["Actual Rows"] == 1, plan
        assert not any(node["Node Type"] == "Seq Scan" for node in nodes(plan))
        assert plan["Shared Hit Blocks"] + plan.get("Shared Read Blocks", 0) < 40


@pytest.mark.parametrize("target", ["analysis", "enrichment"])
def test_terminal_pruned_owner_replay_does_not_load_private_body_or_run_specialists(
    database,
    published_retry_case,
    monkeypatch,
    target,
):
    from devfeed_aggregator import analysis_tasks

    case = published_retry_case
    with database.begin() as session:
        case.request(session, target)
        owner = session.scalar(select(ArticleRefresh))
        refresh_id = owner.id
        identifier = owner.analysis_job_id if target == "analysis" else owner.enrichment_job_id
    observed, fetching = [], []

    def discard():
        with database.begin() as session:
            owner = session.get(ArticleRefresh, refresh_id)
            review_refresh(
                session,
                case.article_id,
                refresh_id,
                owner.version,
                action="reject",
                actor={"subject": "editor"},
                note="Discard obsolete draft",
            )
            owner.payload, owner.payload_pruned_at = {}, utcnow()

    def fetch(url):
        fetching.append(url)
        discard()  # The real enrichment claim already committed before this HTTP result.
        return FetchResult(200, PAGE, url)

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and (
            "article_refreshes.payload," in statement
            or "article_analysis_jobs.input_snapshot" in statement
            or "article_contents.text" in statement
        ):
            observed.append(statement.split("FROM", 1)[0])

    engine = database.kw["bind"]
    if target == "analysis":
        discard()
        monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
        monkeypatch.setattr(
            analysis_tasks, "CodexClient", lambda *_: pytest.fail("closed owner spent inference")
        )
        event.listen(engine, "before_cursor_execute", observe)
        try:
            analysis_tasks._analyze(identifier)
        finally:
            event.remove(engine, "before_cursor_execute", observe)
    else:
        monkeypatch.setattr(article_tasks, "session_factory", lambda: database)
        monkeypatch.setattr(article_tasks, "fetch_article_page", fetch)
        # Attach only after the discard callback, so fixture setup does not
        # confuse a worker read with the editor's deliberate payload redaction.
        original = discard

        def discard_and_observe():
            original()
            event.listen(engine, "before_cursor_execute", observe)

        discard = discard_and_observe
        try:
            article_tasks.enrich_article(str(identifier))
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert len(fetching) == 1
    assert not observed
    with database() as session:
        owner = session.get(ArticleRefresh, refresh_id)
        model = ArticleAnalysisJob if target == "analysis" else ArticleEnrichmentJob
        assert session.get(model, identifier).outcome == "superseded"
        assert owner.status == "cancelled" and owner.payload == {}
        article = session.get(Article, case.article_id)
        assert (
            article.publication_status == "published"
            and article.ai_summary == case.body["ai_summary"]
        )
