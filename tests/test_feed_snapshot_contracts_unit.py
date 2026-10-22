"""Feed cursor and snapshot composition; query eligibility/order remain integration contracts."""

import base64
import json
import uuid
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest
from devfeed_api import latest, ordered_feed
from devfeed_core.cache import CacheUnavailable
from devfeed_core.models import Article, ArticleOrigin, Source
from fastapi import HTTPException

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def published_article(index, *, source=None):
    identifier = uuid.UUID(int=index)
    source = source or Source(
        id=uuid.uuid4(),
        name=f"Publisher {index}",
        slug=f"publisher-{index}",
        source_type="publisher",
        approval_status="approved",
        enabled=True,
        feed_url="https://publisher.example/feed",
        website_url="https://publisher.example",
        managed_logo={},
        logo_url=None,
    )
    return Article(
        id=identifier,
        slug=f"article-{index}",
        canonical_url=f"https://publisher.example/{index}",
        title=f"Article {index}",
        summary="A useful developer article.",
        author=None,
        ai_title=None,
        ai_summary=None,
        ai_description=None,
        image_url=None,
        managed_image={},
        language="en",
        content_type="tutorial",
        content_format="article",
        tags=[],
        topic_links=[],
        published_at=NOW - timedelta(minutes=index),
        feed_at=NOW - timedelta(minutes=index),
        discovered_at=NOW - timedelta(hours=2),
        published_to_feed_at=NOW - timedelta(minutes=index),
        review_status="approved",
        publication_status="published",
        sponsor_names=[],
        origins=[
            ArticleOrigin(
                source=source,
                source_id=source.id,
                entry_key=str(identifier),
                original_url=f"https://publisher.example/{index}",
                source_metadata={},
            )
        ],
    )


class SequenceCache:
    """The sequence-cache API, independent of Redis implementation and TTL mechanics."""

    def __init__(self):
        self.entries = {}
        self.writes = []
        self.fail = False

    def read_sequence(self, key, start, stop):
        if self.fail:
            raise CacheUnavailable
        return self.entries.get(key, [])[start : stop + 1]

    def write_sequence(self, key, values, ttl):
        if self.fail:
            raise CacheUnavailable
        self.entries[key] = values
        self.writes.append((key, values, ttl))


class CurrentArticles:
    """Preloaded, already-visible rows; no filtering or ordering is emulated."""

    def __init__(self, *articles):
        self.articles = articles
        self.reads = 0

    def scalars(self, statement):
        self.reads += 1
        return SimpleNamespace(all=lambda: list(self.articles))


def state(*identifiers, after=None, filters=None):
    return {
        "ids": [str(value) for value in identifiers],
        "after": after,
        "as_of": NOW.isoformat(),
        "filter": latest.filter_key(filters or {}),
    }


@pytest.fixture
def cache(monkeypatch):
    value = SequenceCache()
    monkeypatch.setattr(latest, "get_cache", lambda: value)
    return value


def test_first_snapshot_page_keeps_snapshot_order_and_uses_only_current_read_results(
    cache, monkeypatch
):
    first, removed, third, fourth = [published_article(i) for i in (1, 2, 3, 4)]
    snapshot = Mock(return_value=state(first.id, removed.id, third.id, fourth.id))
    monkeypatch.setattr(latest, "snapshot", snapshot)
    session = CurrentArticles(third, first)
    page = latest.latest_page(session, [], {}, 3, None)
    assert [item.id for item in page.items] == [first.id, third.id]
    assert page.next_cursor.endswith(":3")
    assert cache.writes[0][2] == latest.RETENTION_SECONDS
    generation = page.next_cursor.split(":")[1]
    assert uuid.UUID(generation)
    assert snapshot.call_count == 1 and session.reads == 1
    next_page = latest.latest_page(CurrentArticles(fourth), [], {}, 3, page.next_cursor)
    assert [item.id for item in next_page.items] == [fourth.id]
    assert next_page.next_cursor is None and snapshot.call_count == 1


@pytest.mark.parametrize(
    "cursor",
    [
        "old-cursor",
        "latest-v1:bad:0",
        f"latest-v1:{uuid.uuid4()}:-1",
        f"latest-v1:{uuid.uuid4()}:481",
    ],
)
def test_invalid_snapshot_cursor_is_rejected_before_article_reads(cache, cursor):
    session = CurrentArticles()
    with pytest.raises(HTTPException) as raised:
        latest.latest_page(session, [], {}, 2, cursor)
    assert (raised.value.status_code, raised.value.detail) == (422, "Invalid feed cursor")
    assert session.reads == 0 and cache.writes == []


def test_missing_cached_generation_requires_refresh_instead_of_rebuilding_it(cache, monkeypatch):
    builder = Mock(side_effect=AssertionError("Old generations are immutable"))
    monkeypatch.setattr(latest, "snapshot", builder)
    with pytest.raises(HTTPException) as raised:
        latest.latest_page(CurrentArticles(), [], {}, 2, f"latest-v1:{uuid.uuid4()}:0")
    assert raised.value.status_code == 409
    builder.assert_not_called()


@pytest.mark.parametrize("mismatch", ["filters", "position"])
def test_cached_snapshot_rejects_changed_filters_and_offsets_beyond_the_batch(cache, mismatch):
    generation = uuid.uuid4()
    cache.entries[f"latest-v1:{generation}"] = [json.dumps(state(uuid.uuid4())).encode()]
    with pytest.raises(HTTPException) as raised:
        latest.latest_page(
            CurrentArticles(),
            [],
            {"languages": ["fr"]} if mismatch == "filters" else {},
            2,
            f"latest-v1:{generation}:{2 if mismatch == 'position' else 0}",
        )
    assert raised.value.status_code == 422


def test_crossing_a_batch_boundary_reuses_as_of_and_replays_the_same_next_generation(
    cache, monkeypatch
):
    generation = uuid.uuid4()
    next_generation = uuid.uuid5(generation, "next")
    after = [(NOW - timedelta(minutes=1)).isoformat(), str(uuid.uuid4())]
    cache.entries[f"latest-v1:{generation}"] = [
        json.dumps(state(uuid.uuid4(), after=after)).encode()
    ]
    article = published_article(3)
    builder = Mock(return_value=state(article.id))
    monkeypatch.setattr(latest, "snapshot", builder)
    cursor = f"latest-v1:{generation}:1"
    first = latest.latest_page(CurrentArticles(article), [], {}, 2, cursor)
    repeated = latest.latest_page(CurrentArticles(article), [], {}, 2, cursor)
    assert first == repeated and [item.id for item in first.items] == [article.id]
    builder.assert_called_once_with(
        ANY,
        [],
        latest.filter_key({}),
        as_of=NOW,
        after=after,
    )
    assert cache.writes[0][0] == f"latest-v1:{next_generation}"


def test_disappearing_next_batch_is_an_expired_cursor_instead_of_an_empty_page(cache, monkeypatch):
    generation = uuid.uuid4()
    cache.entries[f"latest-v1:{generation}"] = [
        json.dumps(state(after=[NOW.isoformat(), str(uuid.uuid4())])).encode()
    ]
    monkeypatch.setattr(latest, "snapshot", Mock(return_value=state()))
    cache.write_sequence = Mock()
    with pytest.raises(HTTPException) as raised:
        latest.latest_page(CurrentArticles(), [], {}, 2, f"latest-v1:{generation}:0")
    assert raised.value.status_code == 409


@pytest.mark.parametrize("operation", ["read", "write"])
def test_cache_outage_is_a_retryable_feed_error_without_article_reads(
    cache, monkeypatch, operation
):
    session = CurrentArticles()
    if operation == "read":
        cache.fail = True
        cursor = f"latest-v1:{uuid.uuid4()}:0"
    else:
        monkeypatch.setattr(latest, "snapshot", Mock(return_value=state()))
        cache.fail = True
        cursor = None
    with pytest.raises(HTTPException) as raised:
        latest.latest_page(session, [], {}, 2, cursor)
    assert (raised.value.status_code, raised.value.detail) == (
        503,
        "Latest feed is temporarily unavailable",
    )
    assert session.reads == 0


def test_empty_snapshot_finishes_without_fetching_article_rows(cache, monkeypatch):
    monkeypatch.setattr(latest, "snapshot", Mock(return_value=state()))
    session = CurrentArticles()
    page = latest.latest_page(session, [], {}, 2, None)
    assert page.items == [] and page.next_cursor is None and session.reads == 0


class SnapshotReads:
    def __init__(self, rows, publishers=()):
        self.results = iter([rows, publishers])
        self.calls = 0

    def execute(self, _):
        self.calls += 1
        return SimpleNamespace(all=lambda: next(self.results))


def test_snapshot_records_the_fixed_cutoff_and_last_raw_row_for_the_next_batch(monkeypatch):
    row = namedtuple("Candidate", "id feed_at")
    candidates = [row(uuid.UUID(int=i), NOW - timedelta(minutes=i)) for i in range(1, 5)]
    session = SnapshotReads(candidates, [(r.id, uuid.UUID(int=100)) for r in candidates[:3]])
    monkeypatch.setattr(latest, "BATCH_SIZE", 3)
    result = latest.snapshot(
        session, [], "filter", as_of=NOW, after=[NOW.isoformat(), str(uuid.uuid4())]
    )
    assert result == {
        "ids": [str(r.id) for r in candidates[:3]],
        "after": [candidates[2].feed_at.isoformat(), str(candidates[2].id)],
        "as_of": NOW.isoformat(),
        "filter": "filter",
    }
    assert session.calls == 2


def test_empty_snapshot_does_not_request_publisher_rows(monkeypatch):
    monkeypatch.setattr(latest, "utcnow", lambda: NOW)
    session = SnapshotReads([])
    assert latest.snapshot(session, [], "filter") == {
        "ids": [],
        "after": None,
        "as_of": NOW.isoformat(),
        "filter": "filter",
    }
    assert session.calls == 1


class OrderedRows:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.reads = 0

    def execute(self, _):
        self.reads += 1
        return SimpleNamespace(all=lambda: self.rows)


def unpack(cursor):
    return json.loads(base64.urlsafe_b64decode(cursor[len("sorted-v1:") :]))


def pack(values):
    return "sorted-v1:" + base64.urlsafe_b64encode(json.dumps(values).encode()).decode()


@pytest.mark.parametrize("sort", ["oldest", "most_liked"])
def test_ordered_page_limits_results_and_anchors_the_cursor_at_the_last_visible_item(sort):
    first, second, extra = [
        published_article(i) for i in ((3, 2, 1) if sort == "oldest" else (1, 2, 3))
    ]
    score, next_score = (0, 0) if sort == "oldest" else (9, 8)
    session = OrderedRows((first, score), (second, score), (extra, next_score))
    page = ordered_feed.ordered_page(session, [], {"language": "en"}, sort, 2, None)
    assert [item.id for item in page.items] == [first.id, second.id]
    signature, count, timestamp, identity = unpack(page.next_cursor)
    assert count == score and timestamp == second.feed_at.isoformat() and identity == str(second.id)
    continuation = OrderedRows((extra, next_score))
    result = ordered_feed.ordered_page(
        continuation, [], {"language": "en"}, sort, 2, page.next_cursor
    )
    assert [item.id for item in result.items] == [extra.id] and result.next_cursor is None
    assert signature


@pytest.mark.parametrize("sort,filters", [("most_liked", {}), ("oldest", {"language": "fr"})])
def test_ordered_cursor_cannot_be_reused_with_different_sort_or_filters(sort, filters):
    article = published_article(1)
    page = ordered_feed.ordered_page(
        OrderedRows((published_article(2), 0), (article, 0)), [], {}, "oldest", 1, None
    )
    session = OrderedRows()
    with pytest.raises(HTTPException) as raised:
        ordered_feed.ordered_page(session, [], filters, sort, 1, page.next_cursor)
    assert raised.value.status_code == 422 and session.reads == 0


@pytest.mark.parametrize(
    "field,value", [(1, True), (1, -1), (2, "2026-10-22T12:00:00"), (3, "not-a-uuid")]
)
def test_ordered_cursor_requires_nonnegative_integer_score_aware_time_and_uuid(field, value):
    page = ordered_feed.ordered_page(
        OrderedRows((published_article(2), 0), (published_article(1), 0)), [], {}, "oldest", 1, None
    )
    values = unpack(page.next_cursor)
    values[field] = value
    session = OrderedRows()
    with pytest.raises(HTTPException) as raised:
        ordered_feed.ordered_page(session, [], {}, "oldest", 1, pack(values))
    assert raised.value.status_code == 422 and session.reads == 0


@pytest.mark.parametrize("cursor", ["latest-v1:not-sorted:0", "sorted-v1:!", pack([1, 2])])
def test_malformed_sorted_cursors_never_read_the_feed(cursor):
    session = OrderedRows()
    with pytest.raises(HTTPException) as raised:
        ordered_feed.ordered_page(session, [], {}, "oldest", 1, cursor)
    assert raised.value.status_code == 422 and session.reads == 0
