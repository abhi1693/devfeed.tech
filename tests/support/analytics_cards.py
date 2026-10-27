"""Real operator-card publication in a unique disposable ClickHouse namespace."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from devfeed_admin_api.analytics_runtime import AnalyticsRuntime, get_analytics
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_card_builders import article_rows, proposal_rows, source_rows
from devfeed_core.analytics_direct_cards import normalize_card
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_status import AnalyticsStatus
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    Source,
    SourceCandidate,
    TopicProposal,
    TopicRelationProposal,
)
from sqlalchemy import select

from tests.support import analytics


class CardPublication:
    def __init__(self, database, http, settings):
        self.database, self.http, self.settings = database, http, settings
        self.runtime = AnalyticsRuntime(settings)
        self.generation = 0
        self.identities = set()

    def __call__(self, database=None, *, batch=200):
        database = database or self.database
        now = datetime.now(UTC)
        policy = pipeline_policy_version()
        self.runtime.policy = policy
        self.generation += 1
        batch = min(batch, 200)
        with database() as session:
            identifiers = {
                kind: list(session.scalars(select(model.id).order_by(model.id)))
                for kind, model in (
                    ("article", Article),
                    ("source", Source),
                    ("candidate", SourceCandidate),
                    ("topic-proposal", TopicProposal),
                    ("relationship-proposal", TopicRelationProposal),
                )
            }
        built = []
        for kind, keys in identifiers.items():
            for offset in range(0, len(keys), batch):
                selected = keys[offset : offset + batch]
                if kind == "article":
                    built.extend(article_rows(database, selected, policy, now))
                elif kind in {"source", "candidate"}:
                    built.extend(
                        source_rows(
                            database,
                            selected if kind == "source" else [],
                            selected if kind == "candidate" else [],
                            policy,
                            now,
                        )
                    )
                else:
                    built.extend(
                        proposal_rows(
                            database,
                            selected if kind == "topic-proposal" else [],
                            selected if kind == "relationship-proposal" else [],
                            policy,
                            now,
                        )
                    )
        rows = [
            normalize_card(row, policy, row["kind"], row["entity_id"], self.generation, now)
            for row in built
        ]

        current = {(row["kind"], str(row["entity_id"])) for row in rows}
        rows.extend(
            normalize_card(None, policy, kind, identity, self.generation, now)
            for kind, identity in self.identities - current
        )
        self.identities = current

        async def publish():
            async with AnalyticsClient(self.settings) as client:
                client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                await client.initialize()
                await client.insert("analytics_card_canonical", rows)
                status = AnalyticsStatus(client)
                await status.write(ready=True, cutover=now, policy=policy)

        asyncio.run(publish())
        self.runtime.cache.clear()
        self.runtime.cache_bytes = 0
        return rows

    def publish_history(self):
        now = datetime.now(UTC)

        async def publish():
            async with AnalyticsClient(self.settings) as client:
                with self.database() as session:
                    await analytics._snapshot(
                        client,
                        session,
                        now,
                        observations=getattr(self.database, "analytics_observations", None),
                    )

        asyncio.run(publish())
        self.runtime.cache.clear()
        self.runtime.cache_bytes = 0

    def section(self, group, window):
        return self.http.portal.call(self.runtime.section, group, window)


@pytest.fixture
def card_publication(database, admin_client):
    configured = analytics.settings()
    name = f"devfeed_cards_{uuid.uuid4().hex}_test"

    async def namespace(statement):
        async with AnalyticsClient(configured) as client:
            await client.execute(statement)

    asyncio.run(namespace(f"CREATE DATABASE `{name}`"))
    configured = get_settings().model_copy(
        update={
            "clickhouse_url": configured.clickhouse_url,
            "clickhouse_database": name,
            "clickhouse_username": configured.clickhouse_username,
            "clickhouse_password": configured.clickhouse_password,
            "cache_enabled": False,
        }
    )
    publication = CardPublication(database, admin_client, configured)
    admin_client.app.dependency_overrides[get_analytics] = lambda: publication.runtime
    try:
        yield publication
    finally:
        admin_client.app.dependency_overrides.pop(get_analytics, None)
        admin_client.portal.call(publication.runtime.close)
        asyncio.run(namespace(f"DROP DATABASE `{name}` SYNC"))
