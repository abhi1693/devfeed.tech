"""Trending delegates exact rank and deep offsets to one ClickHouse query."""

import asyncio
import struct
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_user_api import trending

from tests.api.test_feed_snapshot_contracts_unit import published_article


def test_one_query_preserves_rank_visibility_and_numeric_pagination(monkeypatch):
    async def run():
        articles = {str(uuid.UUID(int=i)): published_article(i) for i in range(1, 11)}
        likes = {str(uuid.UUID(int=i)): count for i, count in [(1, 1), (3, 2), (5, 1), (9, 2)]}
        opens = {
            str(uuid.UUID(int=i)): count
            for i, count in [(2, 2), (3, 1), (4, 3), (5, 2), (8, 6), (10, 1)]
        }
        hidden = str(uuid.UUID(int=8))
        scores = {
            key: 3 * likes.get(key, 0) + opens.get(key, 0) for key in likes.keys() | opens.keys()
        }
        expected = sorted(
            (key for key in scores if key != hidden),
            key=lambda key: (scores[key], articles[key].feed_at, key),
            reverse=True,
        )
        metadata_calls, query_calls = [], []

        def metadata(_session, since):
            metadata_calls.append(since)
            return b"snapshot"

        def fetch(_session, identities):
            return [articles[key] for key in identities]

        class Opens:
            @property
            def client(self):
                return self

            @asynccontextmanager
            async def external_query_slot(self):
                yield

            async def trending(self, since, data, *, limit, cursor):
                assert data == b"snapshot"
                query_calls.append(cursor)
                return [{"article_id": key} for key in expected[cursor : cursor + limit + 1]]

        monkeypatch.setattr(trending, "_ranking_metadata", metadata)
        monkeypatch.setattr(trending, "_articles", fetch)
        result, cursor = [], 0
        while True:
            response = await trending.trending_page(
                lambda: nullcontext(None),
                Opens(),
                since=datetime(2026, 10, 1, tzinfo=UTC),
                limit=2,
                cursor=cursor,
            )
            result.extend(str(row.id) for row in response.items)
            if response.next_cursor is None:
                break
            cursor = int(response.next_cursor)
        assert result == expected
        assert len(query_calls) == len(metadata_calls)
        assert query_calls == list(range(0, len(expected), 2))

    asyncio.run(run())


def test_postgres_metadata_uses_bounded_stream_and_closes_before_clickhouse(monkeypatch):
    identity = uuid.uuid4()
    session = SimpleNamespace(
        execute=Mock(
            return_value=[
                SimpleNamespace(id=identity, feed_at=datetime(2026, 1, 1, tzinfo=UTC), likes=2)
            ]
        ),
        rollback=Mock(),
    )
    payload = trending._ranking_metadata(session, datetime(2025, 12, 25, tzinfo=UTC))
    high, low, _, likes = struct.unpack("<QQqQ", payload)
    assert (high << 64) | low == identity.int
    assert likes == 2 and len(payload) == 32
    session.rollback.assert_called_once()
    statement = session.execute.call_args.args[0]
    assert statement.get_execution_options()["yield_per"] == 1000
    monkeypatch.setattr(trending, "MAX_METADATA_BYTES", 1)
    with pytest.raises(AnalyticsUnavailable, match="memory budget"):
        trending._ranking_metadata(session, datetime(2025, 12, 25, tzinfo=UTC))
    assert session.rollback.call_count == 2


def test_concurrent_trending_requests_do_not_buffer_parallel_metadata(monkeypatch):
    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.config import Settings

    async def run():
        client = AnalyticsClient(
            Settings(
                _env_file=None,
                database_url="postgresql://unit@database.invalid/unit_test",
                redis_url="redis://redis.invalid/15",
            )
        )
        snapshots = 0
        maximum = 0

        def metadata(_session, _since):
            nonlocal snapshots, maximum
            snapshots += 1
            maximum = max(maximum, snapshots)
            return b"snapshot"

        class Opens:
            async def trending(self, *_args, **_kwargs):
                nonlocal snapshots
                await asyncio.sleep(0.005)
                snapshots -= 1
                return []

        analytics = Opens()
        analytics.client = client
        monkeypatch.setattr(trending, "_ranking_metadata", metadata)
        monkeypatch.setattr(trending, "_articles", lambda _session, _ids: [])
        await asyncio.gather(
            *(
                trending.trending_page(
                    lambda: nullcontext(None),
                    analytics,
                    since=datetime.now(UTC),
                    limit=10,
                    cursor=0,
                )
                for _ in range(5)
            )
        )
        assert maximum == 1 and snapshots == 0

    asyncio.run(run())
