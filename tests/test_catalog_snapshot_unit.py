"""Revision-cache safety and fallback policy independent of PostgreSQL/Redis I/O."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import catalog_cache
from devfeed_core.cache import CacheUnavailable

REVISIONS = [("tags", "tag-revision"), ("topics", "topic-revision")]
NAMES = ("topics", "tags")


@pytest.fixture
def snapshot(monkeypatch):
    session = Mock()
    session.execute.side_effect = [REVISIONS, REVISIONS]
    cache = SimpleNamespace(namespace="test", redis=Mock(), _run=lambda call: call())
    cache.redis.get.return_value = None
    loader = Mock(return_value=[{"name": "Python"}])
    metrics = Mock()
    monkeypatch.setattr(catalog_cache, "get_settings", lambda: SimpleNamespace(cache_enabled=True))
    monkeypatch.setattr(catalog_cache, "get_cache", lambda: cache)
    monkeypatch.setattr(catalog_cache, "record_cache_read", metrics)
    return SimpleNamespace(session=session, cache=cache, loader=loader, metrics=metrics)


def test_matching_revisions_reuse_cached_catalog_without_loading_database_rows(snapshot):
    snapshot.cache.redis.get.return_value = json.dumps({"revision": REVISIONS, "value": []})
    assert catalog_cache.snapshot(snapshot.session, NAMES, snapshot.loader) == []
    snapshot.loader.assert_not_called()
    snapshot.cache.redis.set.assert_not_called()
    assert snapshot.session.execute.call_count == 1
    snapshot.metrics.assert_called_once_with("catalog", "hit")


@pytest.mark.parametrize(
    "body,reason",
    [
        (None, None),
        (json.dumps({"revision": [["tags", "old"], ["topics", "old"]], "value": []}), None),
        (b"\xff", "invalid_entry"),
        ("not json", "invalid_entry"),
        ("{}", "invalid_entry"),
        ("[]", "invalid_entry"),
        (json.dumps({"revision": REVISIONS}), "invalid_entry"),
    ],
)
def test_misses_and_invalid_entries_reload_and_publish_only_the_current_revision(
    snapshot, body, reason
):
    snapshot.cache.redis.get.return_value = body
    assert (
        catalog_cache.snapshot(snapshot.session, NAMES, snapshot.loader)
        == snapshot.loader.return_value
    )
    snapshot.loader.assert_called_once_with()
    snapshot.session.flush.assert_called_once_with()
    snapshot.metrics.assert_called_once_with("catalog", "miss", reason)
    args, kwargs = snapshot.cache.redis.set.call_args
    assert args[0] == "test:catalog:v1:tags,topics"
    assert json.loads(args[1]) == {
        "revision": [list(row) for row in REVISIONS],
        "value": snapshot.loader.return_value,
    }
    assert kwargs == {"ex": 600}


def test_concurrent_catalog_edit_never_publishes_a_mixed_snapshot(snapshot):
    snapshot.session.execute.side_effect = [REVISIONS, [("tags", "new"), REVISIONS[1]]]
    assert (
        catalog_cache.snapshot(snapshot.session, NAMES, snapshot.loader)
        == snapshot.loader.return_value
    )
    snapshot.cache.redis.set.assert_not_called()


@pytest.mark.parametrize("failed_operation", ["get", "set"])
def test_cache_outage_keeps_database_catalog_available(snapshot, failed_operation):
    getattr(snapshot.cache.redis, failed_operation).side_effect = CacheUnavailable
    assert (
        catalog_cache.snapshot(snapshot.session, NAMES, snapshot.loader)
        == snapshot.loader.return_value
    )
    snapshot.loader.assert_called_once_with()
    if failed_operation == "get":
        snapshot.metrics.assert_called_once_with("catalog", "bypass", "cache_unavailable")


def test_missing_revisions_bypass_storage_without_guessing_cache_freshness(snapshot):
    snapshot.session.execute.side_effect = [REVISIONS[:1]]
    assert (
        catalog_cache.snapshot(snapshot.session, NAMES, snapshot.loader)
        == snapshot.loader.return_value
    )
    snapshot.cache.redis.get.assert_not_called()
    snapshot.cache.redis.set.assert_not_called()


def test_disabled_cache_uses_loader_without_flushing_or_querying_revisions(snapshot, monkeypatch):
    monkeypatch.setattr(catalog_cache, "get_settings", lambda: SimpleNamespace(cache_enabled=False))
    assert (
        catalog_cache.snapshot(snapshot.session, NAMES, snapshot.loader)
        == snapshot.loader.return_value
    )
    snapshot.session.flush.assert_not_called()
    snapshot.session.execute.assert_not_called()
    snapshot.metrics.assert_called_once_with("catalog", "bypass", "disabled")


def test_database_failure_is_not_converted_to_a_successful_cache_response(snapshot):
    snapshot.loader.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        catalog_cache.snapshot(snapshot.session, NAMES, snapshot.loader)
    snapshot.cache.redis.set.assert_not_called()
