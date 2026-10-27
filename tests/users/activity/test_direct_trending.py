"""Trending keeps exact rank and pagination across independently paged stores."""

import asyncio
import uuid
from datetime import UTC, datetime

from devfeed_user_api import trending

from tests.api.test_feed_snapshot_contracts_unit import published_article


def test_likes_and_opens_merge_across_pages_with_visibility_and_stable_ties(monkeypatch):
    async def run():
        monkeypatch.setattr(trending, "PAGE_SIZE", 3)
        articles = {str(uuid.UUID(int=i)): published_article(i) for i in range(1, 11)}
        likes = {str(uuid.UUID(int=i)): count for i, count in [(1, 1), (3, 2), (5, 1), (9, 2)]}
        opens = {
            str(uuid.UUID(int=i)): count
            for i, count in [(2, 2), (3, 1), (4, 3), (5, 2), (8, 6), (10, 1)]
        }
        hidden = str(uuid.UUID(int=8))

        def page(_session, _since, after):
            return [(key, likes[key]) for key in sorted(likes) if after is None or key > after][:3]

        def visible(_session, ids):
            return {key: articles[key].feed_at for key in ids if key != hidden}

        def fetch(_session, ids):
            return [articles[key] for key in ids]

        class Opens:
            async def recent_page(self, _since, *, after, limit):
                return [
                    {"article_id": key, "opens": opens[key]}
                    for key in sorted(opens)
                    if after is None or key > after
                ][:limit]

        monkeypatch.setattr(trending, "_likes_page", page)
        monkeypatch.setattr(trending, "_visible", visible)
        monkeypatch.setattr(trending, "_articles", fetch)
        scores = {
            key: 3 * likes.get(key, 0) + opens.get(key, 0) for key in likes.keys() | opens.keys()
        }
        expected = sorted(
            (key for key in scores if key != hidden),
            key=lambda key: (scores[key], articles[key].feed_at, key),
            reverse=True,
        )
        result = []
        cursor = 0
        while True:
            response = await trending.trending_page(
                None, Opens(), since=datetime(2026, 10, 1, tzinfo=UTC), limit=2, cursor=cursor
            )
            result.extend(str(item.id) for item in response.items)
            if response.next_cursor is None:
                break
            cursor = int(response.next_cursor)
        assert result == expected
        assert hidden not in result

    asyncio.run(run())


def test_paged_postgres_reads_release_their_connection_before_clickhouse():
    from types import SimpleNamespace
    from unittest.mock import Mock

    identity = uuid.uuid4()
    session = SimpleNamespace(
        execute=Mock(return_value=[SimpleNamespace(identity=identity, likes=2)]),
        rollback=Mock(),
    )
    since = datetime(2026, 10, 1, tzinfo=UTC)
    assert trending._likes_page(session, since, None) == [(str(identity), 2)]
    session.rollback.assert_called_once()
    session.execute = Mock(side_effect=RuntimeError("database unavailable"))
    try:
        trending._likes_page(session, since, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected the database failure")
    assert session.rollback.call_count == 2
