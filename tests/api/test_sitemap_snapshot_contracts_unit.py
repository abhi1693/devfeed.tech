"""Snapshot composition; SQL eligibility and Lua atomicity have integration coverage."""

import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_api import sitemaps as routes
from devfeed_core import sitemaps
from devfeed_core.cache import RELEASE, CacheUnavailable
from devfeed_core.config import get_settings
from fastapi import HTTPException, Response
from redis import Redis
from sqlalchemy.sql.elements import TextClause

PREFIX = "unit:sitemaps:v1"
NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def cache(monkeypatch):
    client = Mock(spec=Redis)
    client.get.return_value = None
    client.exists.return_value = False
    client.set.return_value = True
    # Commands are acknowledged at the Redis boundary. These tests do not
    # interpret Lua or claim concurrent publication/lease correctness.
    client.eval.return_value = 1
    monkeypatch.setattr(
        sitemaps,
        "get_sitemap_cache",
        lambda: SimpleNamespace(namespace="unit", redis=client, _run=lambda operation: operation()),
    )
    monkeypatch.setattr(
        sitemaps, "time", SimpleNamespace(time=lambda: NOW.timestamp(), monotonic=time.monotonic)
    )
    return client


def retained(**changes):
    return {
        "format": sitemaps.SNAPSHOT_FORMAT,
        "generation": "a" * 32,
        "built_at": NOW.timestamp() - 1,
        "parts": [],
        "latest_publication": {},
        **changes,
    }


def old_snapshot(cache, **changes):
    value = retained(**changes)
    cache.get.return_value = json.dumps(value).encode()
    return value


@pytest.mark.parametrize("state", ["missing", "obsolete", "current"])
def test_manifest_reads_only_a_compatible_retained_snapshot_without_starting_work(
    cache, monkeypatch, state
):
    builder = Mock(side_effect=AssertionError("A crawler must not build snapshots"))
    monkeypatch.setattr(sitemaps, "build_snapshot", builder)
    value = old_snapshot(cache, format=0 if state == "obsolete" else sitemaps.SNAPSHOT_FORMAT)
    if state == "missing":
        cache.get.return_value = None
    if state == "current":
        assert sitemaps.manifest() == value
    else:
        with pytest.raises(sitemaps.SitemapUnavailable, match="being prepared"):
            sitemaps.manifest()
    builder.assert_not_called()
    cache.set.assert_not_called()


@pytest.mark.parametrize("operation", ["manifest", "part"])
def test_cache_read_outages_never_trigger_database_fallback(cache, monkeypatch, operation):
    cache.get.side_effect = CacheUnavailable
    monkeypatch.setattr(sitemaps, "session_factory", Mock(side_effect=AssertionError("DB read")))
    with pytest.raises(sitemaps.SitemapUnavailable, match="temporarily unavailable"):
        sitemaps.manifest() if operation == "manifest" else sitemaps.part("articles", 1, "a" * 32)


@pytest.mark.parametrize("explicit", [False, True])
def test_parts_use_the_requested_generation_or_the_retained_manifest(cache, explicit):
    manifest = retained()
    part = {"paths": ["/articles/a"], "entries": [{"path": "/articles/a"}]}
    generation = "b" * 32 if explicit else manifest["generation"]
    cache.get.side_effect = (
        [json.dumps(part)] if explicit else [json.dumps(manifest), json.dumps(part)]
    )
    assert sitemaps.part("articles", 2, generation if explicit else None) == part
    assert cache.get.call_args.args == (f"{PREFIX}:{generation}:articles:2",)
    assert cache.get.call_count == (1 if explicit else 2)


def test_fresh_inventory_does_not_acquire_a_refresh_lease(cache, monkeypatch):
    previous = old_snapshot(cache)
    builder = Mock()
    monkeypatch.setattr(sitemaps, "build_snapshot", builder)
    assert sitemaps.refresh_snapshot() == previous
    cache.set.assert_not_called()
    cache.eval.assert_not_called()
    builder.assert_not_called()


@pytest.mark.parametrize("has_previous", [False, True])
def test_cooling_down_retains_available_inventory_and_does_not_retry(
    cache, monkeypatch, has_previous
):
    previous = old_snapshot(cache, built_at=0) if has_previous else None
    cache.exists.return_value = True
    builder = Mock()
    monkeypatch.setattr(sitemaps, "build_snapshot", builder)
    if previous:
        assert sitemaps.refresh_snapshot() == previous
    else:
        with pytest.raises(sitemaps.SitemapUnavailable):
            sitemaps.refresh_snapshot()
    builder.assert_not_called()
    cache.set.assert_not_called()
    cache.eval.assert_not_called()


@pytest.mark.parametrize("has_previous", [False, True])
def test_another_refresh_owner_never_causes_duplicate_builds(cache, monkeypatch, has_previous):
    previous = old_snapshot(cache, built_at=0) if has_previous else None
    cache.set.return_value = False
    builder = Mock()
    monkeypatch.setattr(sitemaps, "build_snapshot", builder)
    assert sitemaps.refresh_snapshot() == previous
    builder.assert_not_called()
    cache.eval.assert_not_called()
    assert cache.set.call_args.kwargs == {"nx": True, "ex": sitemaps.LOCK_SECONDS}


def test_inventory_published_during_lease_acquisition_is_reused_and_the_lease_released(
    cache, monkeypatch
):
    current = retained()
    cache.get.side_effect = [None, json.dumps(current)]
    builder = Mock()
    monkeypatch.setattr(sitemaps, "build_snapshot", builder)
    assert sitemaps.refresh_snapshot() == current
    builder.assert_not_called()
    token = cache.set.call_args.args[1]
    cache.eval.assert_called_once_with(RELEASE, 1, PREFIX + ":lock", token)


@pytest.mark.parametrize("previous_state", ["cold", "stale", "obsolete"])
def test_owned_build_failures_cool_down_release_the_lease_and_retain_only_valid_inventory(
    cache, monkeypatch, previous_state
):
    previous = None
    if previous_state != "cold":
        previous = old_snapshot(
            cache,
            built_at=0,
            format=0 if previous_state == "obsolete" else sitemaps.SNAPSHOT_FORMAT,
        )
    monkeypatch.setattr(
        sitemaps, "build_snapshot", Mock(side_effect=RuntimeError("builder failed"))
    )
    if previous_state == "stale":
        assert sitemaps.refresh_snapshot() == previous
    else:
        with pytest.raises(sitemaps.SitemapUnavailable):
            sitemaps.refresh_snapshot()
    assert cache.set.call_args.args == (PREFIX + ":retry", "1")
    assert cache.set.call_args.kwargs == {"ex": 60}
    assert cache.eval.call_args.args[:3] == (RELEASE, 1, PREFIX + ":lock")


def test_successful_owned_build_uses_configured_retention_and_releases_the_lease(
    cache, monkeypatch
):
    current = retained()
    builder = Mock(return_value=current)
    monkeypatch.setattr(sitemaps, "build_snapshot", builder)
    assert sitemaps.refresh_snapshot() == current
    token = cache.set.call_args.args[1]
    builder.assert_called_once_with(PREFIX, token, get_settings().sitemap_retention_seconds)
    cache.eval.assert_called_once_with(RELEASE, 1, PREFIX + ":lock", token)


class PreloadedSnapshot:
    """Supply already-scoped snapshot rows, without evaluating query predicates."""

    def __init__(self, rows):
        self.rows = rows
        self.reads = 0

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        if isinstance(statement, TextClause):
            return None
        self.reads += 1
        assert self.reads == 1
        return iter(self.rows)


def snapshot_rows(*values):
    from collections import namedtuple

    row = namedtuple("SnapshotRow", "kind id slug published_at content_type")
    return [row(*value) for value in values]


def test_shards_escape_paths_keep_per_kind_pages_and_compute_latest_publication(cache, monkeypatch):
    rows = snapshot_rows(
        ("articles", 1, "a/b & c", NOW - timedelta(hours=2), "tutorial"),
        ("articles", 2, "two", NOW, "news"),
        ("articles", 3, "three", NOW - timedelta(hours=3), "tutorial"),
        ("topics", 4, "python", NOW - timedelta(hours=1), None),
    )
    reader = PreloadedSnapshot(rows)
    monkeypatch.setattr(sitemaps, "session_factory", lambda: reader)
    monkeypatch.setattr(sitemaps, "PART_SIZE", 2)
    result = sitemaps.build_snapshot(PREFIX, "owner", 4000)
    assert [(part["kind"], part["page"]) for part in result["parts"]] == [
        ("articles", 1),
        ("articles", 2),
        ("topics", 1),
    ]
    assert result["latest_publication"] == {
        "articles": NOW.isoformat(),
        "feed:tutorial": (NOW - timedelta(hours=2)).isoformat(),
        "feed:news": NOW.isoformat(),
        "topics": (NOW - timedelta(hours=1)).isoformat(),
    }
    first, second, topic = [json.loads(call.args[1]) for call in cache.set.call_args_list]
    assert first["paths"] == ["/articles/a%2Fb%20%26%20c", "/articles/two"]
    assert second["entries"] == [
        {"path": "/articles/three", "lastmod": (NOW - timedelta(hours=3)).isoformat()}
    ]
    assert topic["paths"] == ["/topics/python"]
    assert all(
        call.kwargs["ex"] == sitemaps.LOCK_SECONDS + sitemaps.PREVIOUS_SECONDS
        for call in cache.set.call_args_list
    )
    # The publication command follows every shard write and carries the owner;
    # the integration suite proves the Lua ownership check itself.
    args = cache.eval.call_args.args
    assert args[1] == 5 and args[2:4] == (PREFIX + ":lock", PREFIX + ":manifest")
    assert args[-5] == "owner" and args[-3:] == (4000, 3, sitemaps.PREVIOUS_SECONDS)


def test_unchanged_shard_content_preserves_its_modified_time(cache, monkeypatch):
    reader = PreloadedSnapshot(snapshot_rows(("articles", 1, "one", NOW, "news")))
    monkeypatch.setattr(sitemaps, "session_factory", lambda: reader)
    first = sitemaps.build_snapshot(PREFIX, "first", 4000)
    old = {**first, "parts": [{**first["parts"][0], "modified_at": 123.0}]}
    cache.get.return_value = json.dumps(old)
    reader.reads = 0
    second = sitemaps.build_snapshot(PREFIX, "second", 4000)
    assert second["generation"] != first["generation"]
    assert second["parts"][0]["digest"] == first["parts"][0]["digest"]
    assert second["parts"][0]["modified_at"] == 123.0
    reader.rows[0] = reader.rows[0]._replace(slug="changed")
    reader.reads = 0
    third = sitemaps.build_snapshot(PREFIX, "third", 4000)
    assert third["parts"][0]["digest"] != second["parts"][0]["digest"]
    assert third["parts"][0]["modified_at"] == NOW.timestamp()


@pytest.mark.parametrize("blocker", ["deadline", "index-limit", "lease-expired"])
def test_incomplete_snapshot_never_returns_a_publishable_manifest(cache, monkeypatch, blocker):
    reader = PreloadedSnapshot(snapshot_rows(("articles", 1, "one", NOW, "news")))
    monkeypatch.setattr(sitemaps, "session_factory", lambda: reader)
    if blocker == "deadline":
        monkeypatch.setattr(
            sitemaps.time, "monotonic", Mock(side_effect=[0, sitemaps.BUILD_SECONDS + 1])
        )
    elif blocker == "index-limit":
        monkeypatch.setattr(sitemaps, "MAX_PARTS", 0)
    else:
        cache.eval.return_value = 0
    with pytest.raises(sitemaps.SitemapUnavailable):
        sitemaps.build_snapshot(PREFIX, "owner", 4000)
    if blocker != "lease-expired":
        cache.eval.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_part_route_preserves_old_cached_shape_and_returns_cache_headers(monkeypatch, legacy):
    shard = (
        ["/articles/one"]
        if legacy
        else {"paths": ["/articles/one"], "entries": [{"path": "/articles/one"}]}
    )
    monkeypatch.setattr(sitemaps, "part", Mock(return_value=shard))
    response = Response()
    assert routes.sitemap_part(response, "articles", 1, "a" * 32) == (
        {"paths": shard} if legacy else shard
    )
    assert response.headers["Cache-Control"] == "public, max-age=300, s-maxage=300"


@pytest.mark.parametrize(
    "kind,page", [("admin", 1), ("articles", 0), ("articles", sitemaps.MAX_PARTS + 1)]
)
def test_unknown_or_out_of_range_shards_never_read_cache(monkeypatch, kind, page):
    loader = Mock()
    monkeypatch.setattr(sitemaps, "part", loader)
    with pytest.raises(HTTPException) as raised:
        routes.sitemap_part(Response(), kind, page, None)
    assert raised.value.status_code == 404
    loader.assert_not_called()


@pytest.mark.parametrize("endpoint", ["index", "part"])
def test_crawler_outages_are_noncacheable_and_include_a_retry_hint(monkeypatch, endpoint):
    loader = Mock(side_effect=sitemaps.SitemapUnavailable)
    monkeypatch.setattr(sitemaps, "manifest" if endpoint == "index" else "part", loader)
    with pytest.raises(HTTPException) as raised:
        routes.sitemap_index(Response()) if endpoint == "index" else routes.sitemap_part(
            Response(), "articles", 1, None
        )
    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "5", "Cache-Control": "no-store"}


def test_index_returns_the_retained_manifest_with_a_short_cache_window(monkeypatch):
    value = retained()
    monkeypatch.setattr(sitemaps, "manifest", Mock(return_value=value))
    response = Response()
    assert routes.sitemap_index(response) == value
    assert response.headers["Cache-Control"] == "public, max-age=60, s-maxage=60"


def test_missing_shard_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(sitemaps, "part", Mock(return_value=None))
    with pytest.raises(HTTPException) as raised:
        routes.sitemap_part(Response(), "articles", 1, None)
    assert raised.value.status_code == 404
