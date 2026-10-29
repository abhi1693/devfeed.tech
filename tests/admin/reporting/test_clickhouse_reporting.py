"""Native analytical queries preserve the existing dashboard's metric contracts."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_admin_api.analytics_reports import ANALYTICAL_GROUPS
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleLike,
    ArticleOrigin,
    ArticleTopic,
    IngestionJob,
    Source,
    Topic,
    UserAccount,
    UserSource,
    UserTopic,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def seed_dashboard(session):
    observations = {"opens": [], "inference": []}
    source = Source(
        name="Publisher",
        source_type="publisher",
        approval_status="approved",
        enabled=True,
        feed_url="https://example.invalid/feed",
        consecutive_failures=2,
    )
    topic = Topic(name="Python", slug="python", kind="technology", status="active")
    user = UserAccount(
        issuer="https://identity.invalid",
        subject="analytics-reader",
        organization_id="test",
        name="Reader",
        created_at=NOW - timedelta(days=1),
    )
    articles = [
        Article(
            title=f"Article {number}",
            canonical_url=f"https://example.invalid/articles/{number}",
            url_hash=str(number) * 64,
            review_status="approved",
            publication_status="published",
            content_type="tutorial" if number == 1 else "news",
            discovered_at=NOW - timedelta(days=number + 1),
            published_to_feed_at=NOW - timedelta(days=1, seconds=number * 60),
        )
        for number in (1, 2)
    ]
    session.add_all([source, topic, user, *articles])
    session.flush()
    session.add_all(
        [
            UserSource(user_id=user.id, source_id=source.id),
            UserTopic(user_id=user.id, topic_id=topic.id, created_at=NOW - timedelta(days=1)),
            ArticleLike(user_id=user.id, article_id=articles[0].id),
            IngestionJob(
                source_id=source.id,
                status="failed",
                created_at=NOW - timedelta(days=2),
                finished_at=NOW - timedelta(days=1),
            ),
            ArticleAnalysisJob(
                article_id=articles[0].id,
                status="succeeded",
                created_at=NOW - timedelta(days=2),
                finished_at=NOW - timedelta(days=1),
                duration_ms=1200,
                usage={"totalTokens": 35, "inputTokens": 30, "outputTokens": 5},
            ),
        ]
    )
    for article in articles:
        session.add(
            ArticleTopic(
                article_id=article.id,
                topic_id=topic.id,
                role="primary",
                relevance=1.0,
                evidence="Analytical fixture",
            )
        )
        # Multiple origins must not multiply source publication counts.
        session.add_all(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key=f"{article.id}-{number}",
                original_url=article.canonical_url,
            )
            for number in (1, 2)
        )
        observations["opens"].append(
            dict(article_id=article.id, viewer_key="a" * 64, opened_hour=NOW - timedelta(days=1))
        )
    observations["opens"].append(
        dict(
            article_id=articles[0].id,
            viewer_key="b" * 64,
            opened_hour=NOW - timedelta(days=1),
        )
    )
    for number, tokens in enumerate(
        [
            {"inputTokens": 30, "cachedInputTokens": 10, "outputTokens": 5},
            {"inputTokens": "invalid", "outputTokens": 0},
        ]
    ):
        observations["inference"].append(
            dict(
                id=uuid.uuid4(),
                started_at=NOW - timedelta(days=1),
                finished_at=NOW - timedelta(days=1) + timedelta(seconds=2),
                operation="article_analysis",
                status="returned" if number == 0 else "failed",
                model="test-model",
                reasoning_effort="low",
                request_hash=str(number) * 64,
                tokens=tokens,
                web_searches=number,
                duration_ms=2000,
            )
        )

    return observations


def test_native_sections_preserve_domain_totals_and_do_not_multiply_relationships(database):
    from tests.support.analytics import load_sections

    with database.begin() as session:
        observations = seed_dashboard(session)
    with database() as session:
        results = load_sections(
            session, sorted(ANALYTICAL_GROUPS), 7, NOW, observations=observations
        )
    publishing = results["publishing"].data.summary
    assert publishing.published == 2 and publishing.automated == 0
    # Estimated percentiles stay within the two observed delays; TDigest does
    # not promise the old exact midpoint interpolation for tiny populations.
    assert 86340 <= publishing.p50_seconds <= 172680
    assert publishing.p50_seconds <= publishing.p95_seconds <= 172680
    audience = results["audience"].data
    assert sum(point.opens or 0 for point in audience.activity) == 3
    assert sum(point.new_accounts for point in audience.activity) == 1
    assert next(point for point in audience.activity if point.opens).estimated_readers == 2
    coverage = results["coverage"].data
    assert len(coverage.topics) == 1 and coverage.topics[0].publications == 2
    assert coverage.topics[0].followers == 1
    assert len(coverage.sources) == 1 and coverage.sources[0].publications == 2
    assert coverage.sources[0].consecutive_failures == 2
    processing = results["processing"].data
    assert next(row for row in processing.outcomes if row.kind == "analysis").completed == 1
    assert next(row for row in processing.outcomes if row.kind == "ingestion").failed == 1
    inference = results["inference"].data.summary
    assert inference.calls == 2 and inference.failed == inference.unreported == 1
    assert inference.input_tokens == 30 and inference.output_tokens == 5
    assert inference.cached_input_tokens == 10 and inference.reasoning_tokens == 0


@pytest.mark.parametrize(
    "start,end,grain",
    [
        ("2026-10-01", "2026-10-03", "day"),
        ("2024-01-02", "2025-01-02", "week"),
        ("2023-01-02", "2025-01-02", "month"),
    ],
)
def test_custom_calendar_windows_filter_before_totals_percentiles_and_top_sources(
    database, start, end, grain
):
    from datetime import date

    from devfeed_admin_api.overview_windows import ReportWindow

    from tests.support.analytics import load_sections

    window = ReportWindow(date.fromisoformat(start), date.fromisoformat(end))
    lower = datetime.combine(window.start, datetime.min.time(), tzinfo=UTC)
    upper = datetime.combine(window.end, datetime.min.time(), tzinfo=UTC)
    stamps = [lower - timedelta(microseconds=1), lower, upper - timedelta(microseconds=1), upper]
    sources = [
        Source(
            name=name,
            source_type="publisher",
            approval_status="approved",
            enabled=True,
            feed_url=f"https://{name.lower()}.invalid/feed",
        )
        for name in ("Excluded", "Selected")
    ]
    observations = {"opens": [], "inference": []}
    with database.begin() as session:
        session.add_all(sources)
        session.flush()
        for index, stamp in enumerate(stamps):
            selected = index in (1, 2)
            delay = index * 3600 if selected else 1_000_000
            article = Article(
                title=f"Range article {index}",
                canonical_url=f"https://range.invalid/{index}",
                url_hash="abcd"[index] * 64,
                review_status="approved",
                publication_status="published",
                discovered_at=stamp - timedelta(seconds=delay),
                published_to_feed_at=stamp,
            )
            session.add(article)
            session.flush()
            session.add_all(
                [
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=sources[int(selected)].id,
                        entry_key=str(index),
                        original_url=article.canonical_url,
                    ),
                    ArticleAnalysisJob(
                        article_id=article.id,
                        status="succeeded",
                        outcome="applied",
                        created_at=stamp - timedelta(minutes=1),
                        finished_at=stamp,
                        duration_ms=index * 10_000 if selected else 1_000_000,
                    ),
                ]
            )
            observations["opens"].append(
                dict(
                    article_id=article.id,
                    viewer_key="c" * 64,
                    opened_hour=stamp.replace(minute=0, second=0, microsecond=0),
                )
            )
            observations["inference"].append(
                dict(
                    id=uuid.uuid4(),
                    started_at=stamp,
                    finished_at=stamp,
                    operation="analysis",
                    status="returned",
                    model="Selected" if selected else "Excluded",
                    web_searches=0,
                    request_hash="abcd"[index] * 64,
                    tokens={
                        "inputTokens": index * 100 if selected else 10_000,
                        "outputTokens": 10,
                    },
                    duration_ms=index * 10_000 if selected else 1_000_000,
                )
            )
    results = load_sections(
        database,
        sorted(ANALYTICAL_GROUPS),
        window.days,
        NOW,
        window=window,
        observations=observations,
    )
    publishing = results["publishing"]
    assert publishing.start == window.start and publishing.end == window.inclusive_end
    assert publishing.granularity == grain
    assert publishing.data.summary.published == 2
    assert sum(point.published for point in publishing.data.activity) == 2
    assert (
        3600 <= publishing.data.summary.p50_seconds <= publishing.data.summary.p95_seconds <= 7200
    )
    assert len(publishing.data.activity) <= ({"week": 54, "month": 25}.get(grain, window.days))
    coverage = results["coverage"].data
    assert [(row.name, row.publications) for row in coverage.sources] == [("Selected", 2)]
    assert coverage.source_publications_total == 2
    inference = results["inference"].data
    assert inference.summary.calls == 2 and inference.summary.input_tokens == 300
    assert [(row.name, row.input_tokens) for row in inference.models] == [("Selected", 300)]
    measured = [point for point in inference.activity if point.calls]
    assert all(10_000 <= point.p50_ms <= point.p95_ms <= 20_000 for point in measured)
    processing = results["processing"].data
    assert sum(point.article_analyses for point in processing.throughput) == 2
    assert processing.outcomes[0].completed == 2
    assert 10 <= processing.outcomes[0].p50_seconds <= processing.outcomes[0].p95_seconds <= 20
    audience = results["audience"].data
    if grain == "day":
        assert sum(point.opens or 0 for point in audience.activity) == 2
    else:
        assert all(point.opens is point.estimated_readers is None for point in audience.activity)
