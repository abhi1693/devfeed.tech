import uuid
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_api import latest
from devfeed_core.cache import CacheUnavailable
from devfeed_core.models import Article, ArticleOrigin, Source, utcnow
from devfeed_core.urls import fingerprint


def test_diversity_preserves_recent_bands_and_softens_limits_for_sparse_sources():
    now = utcnow()
    rows = [SimpleNamespace(id=uuid.uuid4(), feed_at=now - timedelta(minutes=i)) for i in range(16)]
    publishers = {row.id: i // 4 for i, row in enumerate(rows)}
    result = latest.diverse_ids(rows, publishers)
    groups = [publishers[uuid.UUID(value)] for value in result]
    assert len(set(result)) == 16
    assert all(a != b for a, b in zip(groups, groups[1:], strict=False))
    assert max(Counter(groups[:8]).values()) == 2
    assert max(Counter(groups[8:]).values()) == 2
    assert result == latest.diverse_ids(rows, publishers)
    rows.append(SimpleNamespace(id=uuid.uuid4(), feed_at=now - timedelta(days=2)))
    publishers = {row.id: 0 for row in rows}
    assert latest.diverse_ids(rows, publishers) == [str(row.id) for row in rows]


@pytest.mark.parametrize("publisher_count", [1, 4, 40, 480])
def test_full_batch_keeps_recency_within_each_publisher(publisher_count):
    now = utcnow()
    rows = [
        SimpleNamespace(id=uuid.UUID(int=i + 1), feed_at=now - timedelta(seconds=i))
        for i in range(latest.BATCH_SIZE)
    ]
    publishers = {row.id: i % publisher_count for i, row in enumerate(rows)}
    result = latest.diverse_ids(rows, publishers)
    assert len(result) == len(set(result)) == len(rows)
    for publisher in range(publisher_count):
        assert [value for value in result if publishers[uuid.UUID(value)] == publisher] == [
            str(row.id) for row in rows if publishers[row.id] == publisher
        ]
    if publisher_count >= 4:
        for offset in range(0, len(result), 8):
            assert (
                max(
                    Counter(
                        publishers[uuid.UUID(value)] for value in result[offset : offset + 8]
                    ).values()
                )
                <= 2
            )
    if publisher_count > 1:
        groups = [publishers[uuid.UUID(value)] for value in result]
        assert all(a != b for a, b in zip(groups, groups[1:], strict=False))


def seed(database, count=24):
    now = utcnow() - timedelta(minutes=1)
    ids = []
    with database.begin() as session:
        sources = [
            Source(
                name=f"Publisher {i}",
                feed_url=f"https://publisher{i}.example/feed",
                source_type="publisher",
                approval_status="approved",
            )
            for i in range(4)
        ]
        session.add_all(sources)
        session.flush()
        for i in range(count):
            url = f"https://publisher.example/article/{i}"
            article = Article(
                title=f"Article {i}",
                canonical_url=url,
                url_hash=fingerprint(url),
                feed_at=now - timedelta(minutes=i),
                published_at=now - timedelta(minutes=i),
                review_status="approved",
                publication_status="published",
                language="en",
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=sources[(i // 6) % 4].id,
                    entry_key=str(i),
                    original_url=url,
                )
            )
            ids.append(article.id)
        source_id = sources[0].id
    return ids, source_id


def page(client, cursor=None, **params):
    response = client.get(
        "/v1/feed",
        params={"diverse": True, "limit": 5, **({"cursor": cursor} if cursor else {}), **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
def test_snapshot_pagination_survives_new_arrivals_and_moderation(client, database):
    ids, source_id = seed(database)
    first = page(client)
    assert len({a["sources"][0]["id"] for a in first["items"][:4]}) >= 3
    repeated = page(client, first["next_cursor"])
    removed = uuid.UUID(repeated["items"][0]["id"])
    with database.begin() as session:
        session.get(Article, removed).publication_status = "unpublished"
        article = Article(
            title="New arrival",
            canonical_url="https://publisher.example/new",
            url_hash=fingerprint("new"),
            feed_at=utcnow(),
            review_status="approved",
            publication_status="published",
        )
        session.add(article)
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source_id,
                entry_key="new",
                original_url=article.canonical_url,
            )
        )
    seen = [a["id"] for a in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        result = page(client, cursor)
        seen.extend(a["id"] for a in result["items"])
        cursor = result["next_cursor"]
    assert len(seen) == len(set(seen))
    assert set(seen) == {str(i) for i in ids if i != removed}
    assert (
        client.get(
            "/v1/feed", params={"diverse": True, "cursor": first["next_cursor"], "language": "fr"}
        ).status_code
        == 422
    )
    generation = first["next_cursor"].split(":")[1]
    cache = latest.get_cache()
    cache.redis.delete(f"{cache.namespace}:feed:latest-v1:{generation}")
    assert (
        client.get("/v1/feed", params={"diverse": True, "cursor": first["next_cursor"]}).status_code
        == 409
    )


@pytest.mark.integration
def test_batch_boundaries_retain_every_article_and_retry_sequence(client, database, monkeypatch):
    monkeypatch.setattr(latest, "BATCH_SIZE", 8)
    ids, _ = seed(database, count=23)
    result = page(client, limit=3)
    seen = []
    while True:
        seen.extend(a["id"] for a in result["items"])
        if not result["next_cursor"]:
            break
        cursor = result["next_cursor"]
        result = page(client, cursor, limit=3)
        assert result == page(client, cursor, limit=3)
    assert len(seen) == len(set(seen)) == len(ids)
    assert set(seen) == {str(i) for i in ids}


@pytest.mark.integration
def test_source_pages_and_legacy_cursors_remain_chronological(client, database):
    ids, source_id = seed(database)
    result = page(client, source_id=str(source_id))
    assert [a["id"] for a in result["items"]] == [str(i) for i in ids[:5]]
    legacy = client.get("/v1/feed", params={"limit": 5}).json()
    continued = page(client, legacy["next_cursor"])
    assert [a["id"] for a in continued["items"]] == [str(i) for i in ids[5:10]]


@pytest.mark.integration
def test_cache_outage_is_retryable_without_changing_order(client, database, monkeypatch):
    seed(database)

    def unavailable(*args):
        raise CacheUnavailable()

    monkeypatch.setattr(latest.get_cache(), "write_sequence", unavailable)
    assert client.get("/v1/feed?diverse=true").status_code == 503
