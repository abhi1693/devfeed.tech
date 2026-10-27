"""Direct ClickHouse opens preserve reader behavior without PostgreSQL analytics rows."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.models import Article, ArticleEngagement, ArticleLike, ArticleOpen
from devfeed_user_api import engagement
from devfeed_user_api.config import Settings
from sqlalchemy import func, select

from tests.support.analytics import settings as analytics_settings
from tests.support.user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.fixture
def direct_client(user_data, database, monkeypatch):
    client, current, first, _, _ = user_data
    settings = analytics_settings()
    target = f"opens_api_{uuid.uuid4().hex}_test"

    async def create():
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": target})
        ) as native:
            native.insert_settings = {"async_insert_busy_timeout_ms": 1}
            store = ArticleOpenAnalytics(native)
            await store.initialize()
            when = datetime.now(UTC)
            await store.begin_import(when)

            class Importer:
                async def complete(self, sources):
                    return sources == ["article_engagement", "article_opens"]

            await store.mark_ready(when, Importer())

    asyncio.run(create())
    native = AnalyticsClient(settings.model_copy(update={"clickhouse_database": target}))
    native.insert_settings = {"async_insert_busy_timeout_ms": 1}
    client.app.state.analytics_client = native
    monkeypatch.setattr(
        engagement,
        "get_settings",
        lambda: Settings(_env_file=None, base_url="http://testserver", cookie_secure=False),
    )
    client.app.dependency_overrides[engagement.optional_user] = lambda: None
    with database() as session:
        ids = {row.title: row.id for row in session.execute(select(Article.title, Article.id))}
    try:
        yield client, current, first, ids, native
    finally:
        client.portal.call(native.close)

        async def drop():
            async with AnalyticsClient(
                settings.model_copy(update={"clickhouse_database": "default"})
            ) as admin:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

        asyncio.run(drop())


def test_direct_open_deduplicates_and_never_creates_pg_analytics_rows(direct_client, database):
    client, current, _, ids, _ = direct_client
    article = ids["Article 000"]
    url = f"/v1/user/articles/{article}/open"
    headers = {"Origin": "http://testserver"}
    first = client.post(url, headers=headers)
    assert first.status_code == 200 and first.json()["opens"] == 1
    assert client.post(url, headers=headers).json()["opens"] == 1
    client.cookies.clear()
    assert client.post(url, headers=headers).json()["opens"] == 2
    assert (
        client.post(f"/v1/user/articles/{ids['Article 113']}/open", headers=headers).status_code
        == 404
    )
    client.app.dependency_overrides[engagement.optional_user] = lambda: current
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post(url, headers=headers), range(4)))
    assert all(result.status_code == 200 for result in results)
    assert (
        client.get("/v1/user/engagement", params={"article_id": str(article)}).json()[0]["opens"]
        == 3
    )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleOpen)) == 0
        assert session.scalar(select(func.count()).select_from(ArticleEngagement)) == 0


def test_direct_trending_merges_likes_and_opens_and_filters_visibility(direct_client, database):
    client, _, first, ids, _ = direct_client
    with database.begin() as session:
        session.add(ArticleLike(article_id=ids["Article 000"], user_id=first))
        session.add(ArticleLike(article_id=ids["Article 110"], user_id=first))
    headers = {"Origin": "http://testserver"}
    assert (
        client.post(f"/v1/user/articles/{ids['Article 001']}/open", headers=headers).status_code
        == 200
    )
    result = client.get("/v1/user/trending", params={"limit": 1})
    assert result.status_code == 200
    assert result.json()["items"][0]["id"] == str(ids["Article 000"])
    next_page = client.get(
        "/v1/user/trending", params={"limit": 1, "cursor": result.json()["next_cursor"]}
    )
    assert next_page.status_code == 200
    assert next_page.json()["items"][0]["id"] == str(ids["Article 001"])
    assert next_page.json()["next_cursor"] is None
