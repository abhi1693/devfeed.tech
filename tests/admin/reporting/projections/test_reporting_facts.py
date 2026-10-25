"""Incremental report facts preserve raw accounting through replay and corrections."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_admin_api.automation import analysis_token_activity, publication_automation
from devfeed_admin_api.overview_insights import interest_coverage, sources_performance
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleOrigin,
    ArticleReview,
    ArticleTopic,
    ReportingArticleFact,
    ReportingDailyFact,
    ReportingDirty,
    ReportingFactState,
    Source,
    Topic,
    UserAccount,
    UserTopic,
)
from devfeed_core.reporting_facts import (
    KIND_MODELS,
    ReportingFactsUnavailable,
    refresh_reporting_facts,
    require_reporting_facts,
)
from sqlalchemy import delete, func, select, text

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
START = NOW.replace(hour=0) - timedelta(days=3)


def settle(database):
    for _ in range(100):
        refresh_reporting_facts(database, limit=2)
        with database() as session:
            complete = session.scalar(
                select(func.count())
                .select_from(ReportingFactState)
                .where(ReportingFactState.complete)
            )
            dirty = session.scalar(select(func.count()).select_from(ReportingDirty))
            if complete == len(KIND_MODELS) and not dirty:
                require_reporting_facts(session)
                return
    raise AssertionError("Facts did not settle")


def article(**values):
    identifier = uuid.uuid4()
    return Article(
        id=identifier,
        title="Publication",
        url_hash=identifier.hex,
        canonical_url=f"https://example.test/{identifier}",
        discovered_at=START - timedelta(seconds=10),
        **values,
    )


def observe(database, function, *arguments):
    with database() as session:
        raw = function(session, *arguments)
    with database() as session:
        session.info["reporting_facts"] = True
        projected = function(session, *arguments)
    assert projected == raw
    return projected


def test_bootstrap_fails_closed_then_preserves_utc_tokens_boundaries_and_replay(database):
    with database.begin() as session:
        content = article()
        session.add(content)
        session.flush()
        for offset, usage, status in [
            (0, {"totalTokens": 10}, "succeeded"),
            (1, {"totalTokens": 0}, "failed"),
            (2, {"totalTokens": "invalid"}, "succeeded"),
            (3, {"totalTokens": "１２"}, "succeeded"),
            (4, {"totalTokens": "9" * 19}, "succeeded"),
            (-1, {"totalTokens": 999}, "succeeded"),
        ]:
            session.add(
                ArticleAnalysisJob(
                    article_id=content.id,
                    status=status,
                    finished_at=START + timedelta(microseconds=offset),
                    usage=usage,
                    duration_ms=7,
                )
            )
        session.add(
            ArticleAnalysisJob(
                article_id=content.id,
                status="succeeded",
                finished_at=NOW + timedelta(microseconds=1),
                usage={"totalTokens": 999},
            )
        )
    with database() as session, pytest.raises(ReportingFactsUnavailable):
        require_reporting_facts(session)
    settle(database)
    points, duration = observe(database, analysis_token_activity, START, NOW)
    assert sum(day.article_analysis for day in points) == 10
    assert sum(day.reported_runs for day in points) == 2
    assert sum(day.unreported_runs for day in points) == 3
    assert duration == 35
    with database.begin() as session:
        session.execute(
            text(
                "INSERT INTO reporting_dirty(kind,entity_id) "
                "SELECT kind,entity_id FROM reporting_job_facts"
            )
        )
    settle(database)
    assert observe(database, analysis_token_activity, START, NOW) == (points, duration)


def test_publication_review_changes_unpublish_origins_and_delete_match_oracle(database):
    with database.begin() as session:
        source = Source(
            id=uuid.uuid4(),
            name="Source",
            source_type="publisher",
            feed_url="https://example.test/feed",
            approval_status="approved",
            enabled=True,
        )
        topic = Topic(
            id=uuid.uuid4(), name="Topic", slug="topic", kind="technology", status="active"
        )
        user = UserAccount(
            id=uuid.uuid4(),
            issuer="https://identity.test",
            subject="reader",
            organization_id="test",
        )
        content = article(
            review_status="approved", publication_status="published", published_to_feed_at=START
        )
        session.add_all([source, topic, user, content])
        session.flush()
        session.add(UserTopic(user_id=user.id, topic_id=topic.id))
        session.add(
            ArticleTopic(
                article_id=content.id,
                topic_id=topic.id,
                role="primary",
                relevance=1,
                evidence="evidence",
            )
        )
        for index in range(2):
            session.add(
                ArticleOrigin(
                    article_id=content.id,
                    source_id=source.id,
                    entry_key=str(index),
                    original_url=content.canonical_url,
                )
            )
        review = ArticleReview(
            id=uuid.uuid4(),
            article_id=content.id,
            revision=1,
            action="publish",
            automation={"policy": "automatic"},
            created_at=START,
        )
        session.add(review)
        content_id, review_id = content.id, review.id
    settle(database)
    assert (
        observe(database, publication_automation, START, NOW)["published_without_intervention"] == 1
    )
    assert observe(database, sources_performance, START - timedelta(days=1), NOW)[2] == 1
    assert observe(database, interest_coverage, START, NOW)[0].publications == 1
    with database.begin() as session:
        # A manual review after publication must not retrospectively disqualify it.
        session.add(
            ArticleReview(
                article_id=content_id,
                revision=2,
                action="approve",
                automation={},
                created_at=START + timedelta(seconds=1),
            )
        )
    settle(database)
    assert (
        observe(database, publication_automation, START, NOW)["published_without_intervention"] == 1
    )
    with database.begin() as session:
        session.get(ArticleReview, review_id).automation = {}
        session.get(Article, content_id).publication_status = "unpublished"
    settle(database)
    output = observe(database, publication_automation, START, NOW)
    assert output["published_in_window"] == 1 and output["published_without_intervention"] == 0
    assert observe(database, sources_performance, START - timedelta(days=1), NOW)[2] == 0
    assert observe(database, interest_coverage, START, NOW)[0].publications == 0
    with database.begin() as session:
        session.execute(delete(Article).where(Article.id == content_id))
    settle(database)
    assert observe(database, publication_automation, START, NOW)["published_in_window"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportingArticleFact)) == 0
        assert session.scalar(select(func.count()).select_from(ReportingDailyFact)) == 0


def test_job_corrections_completion_movement_delete_and_payload_pruning(database):
    with database.begin() as session:
        content = article()
        session.add(content)
        session.flush()
        job = ArticleAnalysisJob(
            article_id=content.id,
            status="failed",
            finished_at=START,
            usage={"totalTokens": 10},
            duration_ms=5,
            input_snapshot={"large": "payload"},
            catalog_snapshot={"catalog": 1},
        )
        session.add(job)
        session.flush()
        job_id = job.id
    settle(database)
    with database.begin() as session:
        job = session.get(ArticleAnalysisJob, job_id)
        job.input_snapshot = {}
        job.catalog_snapshot = {}
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportingDirty)) == 0
    with database.begin() as session:
        job = session.get(ArticleAnalysisJob, job_id)
        job.usage = {"totalTokens": 25}
        job.finished_at = NOW
        job.duration_ms = 9
    settle(database)
    points, duration = observe(database, analysis_token_activity, START, NOW)
    assert points[0].article_analysis == 0 and points[-1].article_analysis == 25
    assert duration == 9
    with database.begin() as session:
        session.execute(delete(ArticleAnalysisJob).where(ArticleAnalysisJob.id == job_id))
    settle(database)
    points, duration = observe(database, analysis_token_activity, START, NOW)
    assert not any(day.article_analysis for day in points) and duration == 0


def test_partial_start_and_session_timezone_preserve_inclusive_window(database):
    with database.begin() as session:
        content = article()
        session.add(content)
        session.flush()
        for offset in (-1, 0, 1):
            session.add(
                ArticleAnalysisJob(
                    article_id=content.id,
                    status="succeeded",
                    finished_at=START + timedelta(hours=12, microseconds=offset),
                    usage={"totalTokens": 1},
                )
            )
    settle(database)
    partial = START + timedelta(hours=12)
    points, _ = observe(database, analysis_token_activity, partial, NOW)
    assert sum(day.article_analysis for day in points) == 2
    with database() as session:
        session.execute(text("SET TIME ZONE 'Pacific/Auckland'"))
        session.info["reporting_facts"] = True
        assert (
            sum(day.article_analysis for day in analysis_token_activity(session, partial, NOW)[0])
            == 2
        )


def test_bootstrap_catches_insertions_behind_keyset_cursor_and_noop_replay(database):
    with database.begin() as session:
        first, last = article(), article()
        first.id, last.id = uuid.UUID(int=10), uuid.UUID(int=20)
        session.add_all([first, last])
    assert refresh_reporting_facts(database, limit=1) == 1
    with database.begin() as session:
        behind = article()
        behind.id = uuid.UUID(int=5)
        session.add(behind)
    settle(database)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ReportingArticleFact)) == 3
        before = list(
            session.execute(
                text(
                    "SELECT article_id,xmin::text FROM reporting_article_facts ORDER BY article_id"
                )
            )
        )
    with database.begin() as session:
        session.execute(
            text(
                "INSERT INTO reporting_dirty(kind,entity_id) "
                "SELECT 'article',article_id FROM reporting_article_facts"
            )
        )
    settle(database)
    with database() as session:
        assert (
            list(
                session.execute(
                    text(
                        "SELECT article_id,xmin::text FROM reporting_article_facts "
                        "ORDER BY article_id"
                    )
                )
            )
            == before
        )


def test_exact_publication_percentiles_include_boundaries_and_exclude_negative_latencies(database):
    from devfeed_core.reporting_facts import publication_latency

    with database.begin() as session:
        for latency in (2, 5, 9, -1):
            content = article(
                review_status="approved", publication_status="published", published_to_feed_at=NOW
            )
            content.discovered_at = NOW - timedelta(seconds=latency)
            session.add(content)
        future = article(published_to_feed_at=NOW + timedelta(microseconds=1))
        session.add(future)
    settle(database)
    with database() as session:
        latency = publication_latency(session, START, START - timedelta(days=3), NOW)
        assert latency[0] == 5 and latency[1] is None
        assert float(latency[2]) == pytest.approx(8.2)
    assert observe(database, publication_automation, START, NOW)["published_in_window"] == 4


def test_compact_daily_reads_match_oracle_and_correct_persisted_article_history(database):
    from devfeed_core.models import OverviewDaily
    from devfeed_core.overview_daily import daily_metrics, read_daily_metrics

    with database.begin() as session:
        content = article(published_to_feed_at=START, content_type="tutorial")
        session.add(content)
        session.add(
            OverviewDaily(
                day=START.date(),
                updated_at=NOW,
                metrics={
                    "added": 999,
                    "published": 999,
                    "accounts": 0,
                    "opens": None,
                    "readers": None,
                    "multi_article_readers": None,
                    "content_types": {"article": 999},
                },
            )
        )
    settle(database)
    observe(database, daily_metrics, START - timedelta(days=1), NOW)
    with database() as session:
        session.info["reporting_facts"] = True
        row = read_daily_metrics(session, START, NOW)[START.date()]
        assert row["published"] == 1 and row["content_types"] == {"tutorial": 1}
        assert row["opens"] is None


def test_source_failure_facts_follow_failed_ingestion_corrections(database):
    from devfeed_core.models import IngestionJob

    with database.begin() as session:
        source = Source(
            id=uuid.uuid4(),
            name="Source",
            source_type="publisher",
            approval_status="approved",
            enabled=True,
            feed_url="https://example.test/feed",
        )
        session.add(source)
        session.flush()
        job = IngestionJob(source_id=source.id, status="failed", finished_at=NOW)
        session.add(job)
        session.flush()
        job_id = job.id
    settle(database)
    assert observe(database, sources_performance, START, NOW)[0][0].fetch_failures == 1
    with database.begin() as session:
        session.get(IngestionJob, job_id).status = "succeeded"
    settle(database)
    assert observe(database, sources_performance, START, NOW)[0][0].fetch_failures == 0


def test_raw_truncate_invalidates_fact_coverage_without_serving_stale_counts(database):
    with database.begin() as session:
        content = article(published_to_feed_at=NOW)
        session.add(content)
    settle(database)
    with database.begin() as session:
        session.execute(text("TRUNCATE articles CASCADE"))
    with database() as session:
        with pytest.raises(ReportingFactsUnavailable):
            require_reporting_facts(session)
        assert session.scalar(select(func.count()).select_from(ReportingArticleFact)) == 0
    settle(database)
    assert observe(database, publication_automation, START, NOW)["published_in_window"] == 0


def test_update_racing_projection_rolls_back_and_replays_new_generation(database):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event
    from sqlalchemy.exc import DBAPIError

    with database.begin() as session:
        content = article()
        session.add(content)
        session.flush()
        job = ArticleAnalysisJob(
            article_id=content.id, status="succeeded", finished_at=START, usage={"totalTokens": 1}
        )
        session.add(job)
        session.flush()
        job_id = job.id
    settle(database)
    with database.begin() as session:
        session.execute(
            text("SELECT reporting_enqueue('article_analysis',:id)").bindparams(id=job_id)
        )
    changed = False
    engine = database.kw["bind"]

    def update():
        with database.begin() as session:
            session.get(ArticleAnalysisJob, job_id).usage = {"totalTokens": 8}

    def race(connection, cursor, statement, parameters, context, many):
        nonlocal changed
        if (
            changed
            or not statement.startswith("SELECT")
            or "FROM article_analysis_jobs" not in statement
        ):
            return
        changed = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(update).result(timeout=5)

    event.listen(engine, "after_cursor_execute", race)
    try:
        with pytest.raises(DBAPIError) as error:
            refresh_reporting_facts(database, limit=2)
        assert error.value.orig.sqlstate == "40001"
    finally:
        event.remove(engine, "after_cursor_execute", race)
    with database() as session:
        assert (
            session.scalar(
                select(ReportingDirty.generation).where(
                    ReportingDirty.kind == "article_analysis", ReportingDirty.entity_id == job_id
                )
            )
            == 2
        )
    settle(database)
    points, _ = observe(database, analysis_token_activity, START, NOW)
    assert sum(point.article_analysis for point in points) == 8


@pytest.mark.parametrize("timestamp", [None, NOW + timedelta(days=1)])
def test_current_topic_inventory_includes_legacy_missing_and_future_publication_times(
    database, timestamp
):
    from devfeed_admin_api.overview import overview_metrics

    with database.begin() as session:
        topic = Topic(
            id=uuid.uuid4(), name="Legacy", slug="legacy", kind="technology", status="active"
        )
        content = article(
            review_status="approved", publication_status="published", published_to_feed_at=timestamp
        )
        session.add_all([topic, content])
        session.flush()
        session.add(
            ArticleTopic(
                article_id=content.id,
                topic_id=topic.id,
                role="primary",
                relevance=1,
                evidence="legacy",
            )
        )
    settle(database)
    with database() as session:
        authoritative = overview_metrics(session, 7)
    with database() as session:
        session.info["reporting_facts"] = True
        projected = overview_metrics(session, 7)
    assert projected.top_topics == authoritative.top_topics
    assert projected.top_topics[0].articles == 1
    assert observe(database, publication_automation, START, NOW)["published_in_window"] == 0
