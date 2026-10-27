"""Disposable ClickHouse reader-analytics fixtures shared by API behavior tests."""

import asyncio
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.models import Article
from devfeed_user_api import engagement
from devfeed_user_api.config import Settings
from sqlalchemy import select

from tests.support.analytics import settings as analytics_settings


@contextmanager
def reader_analytics(client):
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
            await native.initialize()
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
    try:
        yield native
    finally:
        client.portal.call(native.close)

        async def drop():
            async with AnalyticsClient(
                settings.model_copy(update={"clickhouse_database": "default"})
            ) as admin:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

        asyncio.run(drop())


@pytest.fixture
def direct_client(user_data, database, monkeypatch):
    client, current, first, _, _ = user_data
    monkeypatch.setattr(
        engagement,
        "get_settings",
        lambda: Settings(_env_file=None, base_url="http://testserver", cookie_secure=False),
    )
    client.app.dependency_overrides[engagement.optional_user] = lambda: None
    with database() as session:
        ids = {row.title: row.id for row in session.execute(select(Article.title, Article.id))}
    with reader_analytics(client) as native:
        yield client, current, first, ids, native
