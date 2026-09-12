"""Overview aggregates preserve history and avoid multiplying related records."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_admin_api.overview_insights import overview_insights, sources_performance
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOpen,
    ArticleOrigin,
    OverviewDaily,
    Source,
    Topic,
    UserAccount,
    UserRecommendationState,
    UserSource,
    UserTopic,
)
from devfeed_core.overview_daily import read_daily_metrics, refresh_overview_daily
from sqlalchemy import delete

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def seed(session):
    source = Source(
        id=uuid.uuid4(),
        name="Publisher",
        source_type="publisher",
        approval_status="approved",
        enabled=True,
        feed_url="https://example.test/rss",
        consecutive_failures=2,
    )
    topic = Topic(id=uuid.uuid4(), name="Python", slug="python", kind="technology", status="active")
    user = UserAccount(
        id=uuid.uuid4(),
        issuer="https://example.test",
        subject="reader",
        organization_id="test",
        name="Reader",
        created_at=NOW - timedelta(days=1),
    )
    article = Article(
        id=uuid.uuid4(),
        title="Article",
        canonical_url="https://example.test/article",
        url_hash="a" * 64,
        review_status="approved",
        publication_status="published",
        content_type="tutorial",
        discovered_at=NOW - timedelta(days=2),
        published_to_feed_at=NOW - timedelta(days=1),
    )
    session.add_all([source, topic, user, article])
    session.flush()
    session.add_all(
        [
            UserSource(user_id=user.id, source_id=source.id),
            UserTopic(user_id=user.id, topic_id=topic.id),
            ArticleLike(user_id=user.id, article_id=article.id),
        ]
    )
    # Two origins from the same source must still count the article only once.
    session.add_all(
        [
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key=str(i),
                original_url=article.canonical_url,
            )
            for i in range(2)
        ]
    )
    session.add_all(
        [
            ArticleOpen(
                article_id=article.id, viewer_key=str(i), opened_hour=NOW - timedelta(days=1)
            )
            for i in range(3)
        ]
    )
    session.flush()
    return user.id, article.id


def test_counts_windows_coverage_sources_and_inactive_users(database):
    with database.begin() as session:
        user_id, article_id = seed(session)
        session.add(
            UserAccount(
                id=uuid.uuid4(),
                issuer="https://example.test",
                subject="empty",
                organization_id="test",
                created_at=NOW - timedelta(days=10),
            )
        )
    with database() as session:
        result = overview_insights(session, 7, NOW)
        assert result.publications.current == 1 and result.publications.previous == 0
        assert result.accounts.current == 1 and result.accounts.previous == 1
        assert result.opens.current == 3
        assert result.publication_seconds.current == 86400
        assert result.publication_p90_seconds == 86400
        assert result.top_articles[0].id == article_id and result.top_articles[0].likes == 1
        assert result.source_performance[0].discovered == 1
        assert result.source_performance[0].published == 1
        assert result.source_performance[0].followers == 1
        assert result.failing_sources[0].consecutive_failures == 2
        assert result.coverage[0].followers == 1 and result.coverage[0].publications == 0
        assert result.personalization.not_needed == 1 and result.personalization.pending == 1
        assert next(day for day in result.reader_activity if day.published).content_types == {
            "tutorial": 1
        }
        # A previous 30-day window cannot be reconstructed from retained opens.
        assert overview_insights(session, 30, NOW).opens.previous is None


def test_source_output_ranks_publications_before_limiting_sources(database):
    with database.begin() as session:
        seed(session)
        # More than 12 busy sources must not crowd a publishing source out of the chart.
        for i in range(13):
            source = Source(
                id=uuid.uuid4(),
                name=f"Busy {i}",
                source_type="publisher",
                approval_status="approved",
                enabled=True,
                feed_url=f"https://busy{i}.test/feed",
            )
            session.add(source)
            for j in range(2):
                article = Article(
                    id=uuid.uuid4(),
                    title=f"Unpublished {i}-{j}",
                    canonical_url=f"https://busy{i}.test/{j}",
                    url_hash=f"{i * 2 + j:064x}",
                    discovered_at=NOW - timedelta(days=1),
                    publication_status="unpublished",
                )
                session.add(article)
                session.flush()
                session.add(
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=source.id,
                        entry_key=str(j),
                        original_url=article.canonical_url,
                    )
                )
    with database() as session:
        output, _ = sources_performance(session, NOW - timedelta(days=7), NOW)
        assert len(output) == 12
        assert output[0].name == "Publisher"
        assert output[0].published == 1 and output[0].discovered == 1
        assert all(row.published == 0 and row.discovered == 2 for row in output[1:])


def test_rollups_survive_event_cleanup_and_remain_idempotent(database, monkeypatch):
    from devfeed_core import overview_daily

    with database.begin() as session:
        seed(session)
    monkeypatch.setattr(overview_daily, "utcnow", lambda: NOW)
    refresh_overview_daily(database)
    refresh_overview_daily(database)
    with database.begin() as session:
        assert session.get(OverviewDaily, NOW.date() - timedelta(days=1)).metrics["opens"] == 3
        session.execute(delete(ArticleOpen))
    later = NOW + timedelta(days=40)
    monkeypatch.setattr(overview_daily, "utcnow", lambda: later)
    refresh_overview_daily(database)
    with database() as session:
        values = read_daily_metrics(session, NOW - timedelta(days=2), later)
        assert values[(NOW - timedelta(days=1)).date()]["opens"] == 3
        assert values[(NOW - timedelta(days=2)).date()]["opens"] == 0
        # The first backfill never invented data outside the retention window.
        assert session.get(OverviewDaily, NOW.date() - timedelta(days=40)).metrics["opens"] is None


def test_personalization_issues_are_bounded_and_do_not_include_empty_accounts(database):
    with database.begin() as session:
        user_id, _ = seed(session)
        state = session.get(UserRecommendationState, user_id)
        state.next_refresh_at = NOW - timedelta(hours=1)
    with database() as session:
        result = overview_insights(session, 7, NOW)
        assert result.personalization.overdue == 1
        assert result.personalization.issues[0].id == user_id
        assert result.personalization.issues[0].issue == "Refresh overdue"


def test_overview_cache_is_private_scoped_by_range_and_expires(database, admin_client, monkeypatch):
    from devfeed_admin_api import overview
    from devfeed_core.cache import ResponseCache
    from devfeed_core.config import get_settings
    from test_api_query_budgets import profile_request
    from test_cache import MemoryRedis

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    store = ResponseCache(MemoryRedis(), "overview-test")
    monkeypatch.setattr(overview, "get_cache", lambda: store)
    first = admin_client.get("/v1/admin/overview?days=7")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    report, cached = profile_request(admin_client, "/v1/admin/overview?days=7", 0, 1)
    assert report["queries"] == 0 and cached == first.json()
    from devfeed_admin_api import auth
    from devfeed_admin_api.config import Settings
    from devfeed_admin_api.main import create_app
    from fastapi.testclient import TestClient

    auth_settings = Settings(
        _env_file=None,
        admin_base_url="https://admin.example",
        oidc_issuer_url="https://identity.example",
        oidc_client_id="admin-client",
        oidc_organization_id="integration-org",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: auth_settings)
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/v1/admin/overview?days=7").status_code == 401
    assert admin_client.get("/v1/admin/overview?days=30").json()["days"] == 30
    store.redis.now += 61
    report, _ = profile_request(admin_client, "/v1/admin/overview?days=7", 31, 1)
    assert report["queries"] > 0


def test_overview_cache_does_not_start_duplicate_aggregate_work(
    database, admin_client, monkeypatch
):
    from devfeed_admin_api import overview
    from devfeed_core.cache import ResponseCache
    from devfeed_core.config import get_settings
    from test_cache import MemoryRedis

    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    store = ResponseCache(MemoryRedis(), "overview-test")
    monkeypatch.setattr(overview, "get_cache", lambda: store)
    store.lookup("admin-overview-v1:7", "admin-overview")
    response = admin_client.get("/v1/admin/overview?days=7")
    assert response.status_code == 503 and response.headers["retry-after"] == "2"
