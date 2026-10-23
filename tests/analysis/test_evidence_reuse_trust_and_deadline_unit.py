"""Evidence reuse cannot bypass URL policy, freshness, or the caller's deadline."""

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import cache, topic_evidence
from devfeed_core.feeds.fetcher import FeedError
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.support.source_readiness import NOW, configuration

URL = "https://publisher.example/article"


def page(**changes):
    text = changes.pop("text", "A documented engineering source passage.")
    return {
        "text": text,
        "final_url": URL,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "validated_at": NOW.isoformat(),
        "reuse_seconds": 300,
        "reusable": True,
        **changes,
    }


@pytest.fixture
def evidence(monkeypatch):
    client = Mock()
    client.get.return_value = None
    clock = [100.0]
    fresh = page()
    fetch = Mock(return_value=fresh)
    monkeypatch.setattr(cache, "get_cache", lambda: SimpleNamespace(namespace="unit", redis=client))
    monkeypatch.setattr(
        topic_evidence,
        "get_settings",
        lambda: configuration(cache_enabled=True, cache_max_bytes=1024),
    )
    monkeypatch.setattr(topic_evidence, "utcnow", lambda: NOW)
    monkeypatch.setattr(topic_evidence, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(topic_evidence, "fetched_page", fetch)
    return SimpleNamespace(redis=client, clock=clock, fresh=fresh, fetch=fetch)


def test_valid_reuse_preserves_validation_age_and_evidence_identity(evidence):
    saved = page(validated_at=(NOW - timedelta(seconds=30)).isoformat())
    evidence.redis.get.return_value = json.dumps(saved).encode()
    assert topic_evidence.reusable_page(URL, 10) == saved
    evidence.fetch.assert_not_called()
    evidence.redis.set.assert_not_called()
    assert evidence.redis.get.call_args.args == (
        "unit:topic-evidence:v1:" + hashlib.sha256(URL.encode()).hexdigest(),
    )


@pytest.mark.parametrize(
    "problem",
    [
        "json",
        "missing-date",
        "naive-date",
        "future",
        "expired",
        "page-max-age",
        "not-reusable",
        "wrong-text-type",
        "unsafe-final-url",
        "oversized",
        "invalid-reuse-age",
    ],
)
def test_untrusted_or_expired_cached_page_requires_a_fresh_fetch(evidence, problem):
    saved = page(validated_at=(NOW - timedelta(seconds=30)).isoformat())
    if problem == "missing-date":
        del saved["validated_at"]
    elif problem == "naive-date":
        saved["validated_at"] = NOW.replace(tzinfo=None).isoformat()
    elif problem == "future":
        saved["validated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif problem == "expired":
        saved["validated_at"] = (NOW - timedelta(seconds=300)).isoformat()
    elif problem == "page-max-age":
        saved["reuse_seconds"] = 30
    elif problem == "not-reusable":
        saved["reusable"] = False
    elif problem == "wrong-text-type":
        saved["text"] = ["injected"]
    elif problem == "unsafe-final-url":
        saved["final_url"] = "http://127.0.0.1/private"
    elif problem == "invalid-reuse-age":
        saved["reuse_seconds"] = "not-an-age"
    raw = json.dumps(saved).encode()
    if problem == "json":
        raw = b"{malformed"
    elif problem == "oversized":
        raw = json.dumps(page(text="Evidence passage. " * 100)).encode()

    def read(_key):
        evidence.clock[0] += 2
        return raw

    evidence.redis.get.side_effect = read
    assert topic_evidence.reusable_page(URL, 10) is evidence.fresh
    evidence.fetch.assert_called_once_with(URL, 8)


@pytest.mark.parametrize("read_fails", [False, True])
def test_cache_read_consuming_the_deadline_cannot_start_a_fetch_or_return_expired_data(
    evidence, read_fails
):
    def read(_key):
        evidence.clock[0] += 10
        if read_fails:
            raise RedisConnectionError("unavailable")
        return json.dumps(page(validated_at=(NOW - timedelta(seconds=301)).isoformat())).encode()

    evidence.redis.get.side_effect = read
    with pytest.raises(FeedError) as raised:
        topic_evidence.reusable_page(URL, 10)
    assert raised.value.reason == "verification_timeout" and raised.value.retryable is True
    evidence.fetch.assert_not_called()
    evidence.redis.set.assert_not_called()


def test_unavailable_cache_still_allows_fresh_evidence_with_remaining_budget(evidence):
    evidence.redis.get.side_effect = RedisConnectionError("unavailable")
    assert topic_evidence.reusable_page(URL, 10) is evidence.fresh
    evidence.fetch.assert_called_once_with(URL, 10)
    evidence.redis.set.assert_not_called()


def test_failed_fresh_fetch_never_falls_back_to_expired_evidence(evidence):
    evidence.redis.get.return_value = json.dumps(
        page(validated_at=(NOW - timedelta(seconds=301)).isoformat())
    ).encode()
    failure = FeedError("offline", reason="transport_error", retryable=True)
    evidence.fetch.side_effect = failure
    with pytest.raises(FeedError) as raised:
        topic_evidence.reusable_page(URL, 10)
    assert raised.value is failure
    evidence.redis.set.assert_not_called()


def test_optional_cache_write_failure_preserves_the_fresh_fetch_result(evidence):
    evidence.redis.set.side_effect = RedisConnectionError("unavailable")
    assert topic_evidence.reusable_page(URL, 10) is evidence.fresh
    key, raw = evidence.redis.set.call_args.args
    assert key == "unit:topic-evidence:v1:" + hashlib.sha256(URL.encode()).hexdigest()
    assert json.loads(raw) == evidence.fresh
    assert evidence.redis.set.call_args.kwargs == {"ex": 300}


@pytest.mark.parametrize("policy", ["no-reuse", "zero-max-age", "oversized"])
def test_fresh_page_respects_origin_reuse_policy_and_cache_size_limit(evidence, policy):
    evidence.fresh.update(
        page(
            **(
                {"reusable": False}
                if policy == "no-reuse"
                else {"reuse_seconds": 0}
                if policy == "zero-max-age"
                else {"text": "Evidence passage. " * 100}
            )
        )
    )
    assert topic_evidence.reusable_page(URL, 10) is evidence.fresh
    evidence.redis.set.assert_not_called()


@pytest.mark.parametrize("config", [{"cache_enabled": False}, {"topic_evidence_reuse_seconds": 0}])
def test_disabled_reuse_fetches_without_cache_io(evidence, monkeypatch, config):
    monkeypatch.setattr(
        topic_evidence, "get_settings", lambda: configuration(**{"cache_enabled": True, **config})
    )
    assert topic_evidence.reusable_page(URL, 10) is evidence.fresh
    evidence.fetch.assert_called_once_with(URL, 10)
    evidence.redis.get.assert_not_called()
    evidence.redis.set.assert_not_called()


def test_unsafe_requested_url_is_rejected_before_cache_or_network(evidence):
    with pytest.raises(ValueError, match="Private"):
        topic_evidence.reusable_page("http://127.0.0.1/private", 10)
    evidence.redis.get.assert_not_called()
    evidence.fetch.assert_not_called()
