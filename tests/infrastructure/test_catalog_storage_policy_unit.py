"""Disposable cache and payload-retention adapters preserve authoritative receipts.

Preselected retention rows and revision reads are repository boundaries. These
units verify Python redaction, identity checks and Redis calls; they do not emulate
SQL retention filters, transaction isolation, or Redis Lua behavior.
"""

import copy
import hashlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import analysis, cache, catalog_cache, job_dispatch, job_retention
from devfeed_core.article_analysis_steps import RelevanceResult, prepare_article_step
from devfeed_core.cache import CacheUnavailable
from devfeed_core.job_dispatch import DispatchLane
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleEnrichmentJob, TopicAnalysisJob
from devfeed_core.topic_analysis import TopicResearchResult

from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import ACTOR, NOW, proposal


@pytest.fixture
def revisions(monkeypatch):
    v = SimpleNamespace(
        session=Mock(),
        settings=configuration(cache_enabled=True),
        store=cache.ResponseCache(Mock(), "unit:catalog"),
        version=("topics", str(uuid.uuid4())),
    )
    v.session.get_bind.return_value = object()
    v.session.execute.side_effect = lambda _: [v.version]
    v.store.redis.get.return_value = None
    monkeypatch.setattr(catalog_cache, "get_settings", lambda: v.settings)
    monkeypatch.setattr(catalog_cache, "get_cache", lambda: v.store)
    return v


@pytest.mark.parametrize("entry", ["missing", "wrong-revision", "malformed", "unavailable"])
def test_catalog_cache_miss_reads_authority_and_only_publishes_under_the_same_revision(
    revisions,
    entry,
):
    v = revisions
    v.store.redis.get.return_value = {
        "missing": None,
        "wrong-revision": json.dumps(
            {"revision": [["topics", str(uuid.uuid4())]], "value": {"topics": []}}
        ).encode(),
        "malformed": b'{"revision":',
        "unavailable": None,
    }[entry]
    if entry == "unavailable":
        from redis.exceptions import ConnectionError

        v.store.redis.get.side_effect = ConnectionError("unavailable")
    value = {"topics": [{"id": str(uuid.uuid4()), "name": "Angular"}]}
    loader = Mock(return_value=value)
    with catalog_cache.reuse_snapshots():
        assert catalog_cache.snapshot(v.session, ("topics",), loader) is value
        assert catalog_cache.snapshot_current(v.session, ("topics",), value)
        assert not catalog_cache.snapshot_current(v.session, ("topics",), copy.deepcopy(value))
    loader.assert_called_once()
    if entry == "unavailable":
        v.store.redis.set.assert_not_called()
    else:
        body = json.loads(v.store.redis.set.call_args.args[1])
        assert body == {"revision": [list(v.version)], "value": value}
        assert v.store.redis.set.call_args.kwargs == {"ex": 600}


@pytest.mark.parametrize("enabled", [False, True])
def test_revision_change_during_load_cannot_publish_or_authorize_a_mixed_snapshot(
    revisions, enabled
):
    v = revisions
    v.settings.cache_enabled = enabled
    value = {"topics": [{"name": "Angular"}]}
    next_version = ("topics", str(uuid.uuid4()))
    v.session.execute.side_effect = [[v.version], [next_version]]
    with catalog_cache.reuse_snapshots():
        assert catalog_cache.snapshot(v.session, ("topics",), lambda: value) is value
        assert not catalog_cache.snapshot_current(v.session, ("topics",), value)
    v.store.redis.set.assert_not_called()


def test_disabled_redis_still_checks_database_revision_for_tick_local_reuse(revisions):
    v = revisions
    v.settings.cache_enabled = False
    value, load = {"topics": []}, Mock()
    load.return_value = value
    with catalog_cache.reuse_snapshots():
        assert catalog_cache.snapshot(v.session, ("topics",), load) is value
        assert catalog_cache.snapshot(v.session, ("topics",), load) is value
        assert catalog_cache.snapshot_current(v.session, ("topics",), value)
    assert not catalog_cache.snapshot_current(v.session, ("topics",), value)
    load.assert_called_once()
    assert not v.store.redis.mock_calls


def test_joined_snapshot_scopes_preserve_owner_invalidation_and_cleanup(revisions):
    v = revisions
    v.settings.cache_enabled = False
    retained, invalidated = {}, Mock()
    loader = Mock(side_effect=[{"name": "Original"}, {"name": "Changed"}, {}])
    with catalog_cache.reuse_snapshots(retained=retained, on_invalidate=invalidated):
        first = catalog_cache.snapshot(v.session, ("topics",), loader)
        with pytest.raises(RuntimeError), catalog_cache.reuse_snapshots(reuse_existing=True):
            assert catalog_cache.snapshot(v.session, ("topics",), loader) is first
            raise RuntimeError("cancelled nested operation")
        v.version = ("topics", str(uuid.uuid4()))
        with catalog_cache.reuse_snapshots(reuse_existing=True):
            assert catalog_cache.snapshot(v.session, ("topics",), loader)["name"] == "Changed"
        invalidated.assert_called_once()
        # The default keeps independently owned scopes isolated.
        with catalog_cache.reuse_snapshots():
            assert catalog_cache.snapshot(v.session, ("topics",), loader) == {}
        assert len(retained) == 1
    assert catalog_cache._snapshots.get() is None
    assert catalog_cache._invalidator.get() is None
    assert loader.call_count == 3


def test_missing_revision_cannot_be_remembered_or_published(revisions):
    v = revisions
    v.session.execute.return_value = []
    v.session.execute.side_effect = None
    load = Mock(return_value={"topics": []})
    with catalog_cache.reuse_snapshots():
        first = catalog_cache.snapshot(v.session, ("topics",), load)
        assert not catalog_cache.snapshot_current(v.session, ("topics",), first)
        catalog_cache.snapshot(v.session, ("topics",), load)
    assert load.call_count == 2 and not v.store.redis.mock_calls


@pytest.mark.parametrize("bad_generation", [b"bad", b"g" * 32, "a" * 32])
def test_invalid_redis_generation_never_falls_back_to_a_shareable_cache_key(bad_generation):
    redis = Mock()
    redis.get.return_value = bad_generation
    store = cache.ResponseCache(redis, "unit:cache")
    with pytest.raises(CacheUnavailable):
        store.lookup("private request identity", "public")
    redis.set.assert_not_called()
    redis.eval.assert_not_called()


def test_invalidation_rejects_the_feed_storage_domain_before_touching_redis():
    redis = Mock()
    store = cache.ResponseCache(redis, "unit")
    with pytest.raises(ValueError, match="Unknown cache domain"):
        store.invalidate("feed")
    assert not redis.mock_calls


@pytest.mark.parametrize("entries,ttl", [([], 30), ([b"one"] * 501, 30), ([b"one"], 0)])
def test_invalid_feed_sequences_are_rejected_before_redis(entries, ttl):
    redis = Mock()
    with pytest.raises(ValueError, match="Invalid feed sequence"):
        cache.ResponseCache(redis, "unit").write_sequence("opaque-feed", entries, ttl)
    assert not redis.mock_calls


def test_sequence_adapter_passes_exact_ordered_entries_and_private_identity_to_redis():
    redis = MagicMock()
    pipe = redis.pipeline.return_value.__enter__.return_value
    pipe.exists.return_value = pipe.lrange.return_value = pipe
    pipe.execute.return_value = [1, [b"second", b"third"]]
    store = cache.ResponseCache(redis, "unit")
    store.write_sequence("opaque-feed", [b"first", b"second", b"third"], 120)
    arguments = redis.eval.call_args.args
    assert arguments[1:] == (1, "unit:feed:opaque-feed", 120, b"first", b"second", b"third")
    assert store.read_sequence("opaque-feed", 1, 2) == [b"second", b"third"]
    pipe.lrange.assert_called_once_with("unit:feed:opaque-feed", 1, 2)


def test_process_cache_isolates_database_namespace_and_closes_all_owned_pools(monkeypatch):
    settings = configuration(cache_enabled=True)
    connections = []

    options = []

    def connect(*_args, **kwargs):
        value = Mock()
        connections.append(value)
        options.append(kwargs)
        return value

    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    monkeypatch.setattr(cache, "create_redis", connect)
    cache._cache_for_process.cache_clear()
    try:
        current = cache.get_cache()
        assert cache.get_cache() is current
        expected = hashlib.sha256(settings.database_url.encode()).hexdigest()[:16]
        assert current.namespace == "devfeed:cache:v1:" + expected
        assert settings.database_url not in current.namespace
        sitemap = cache.get_sitemap_cache()
        assert cache.get_sitemap_cache() is sitemap
        assert sitemap is not current and sitemap.namespace == current.namespace
        sequences = cache.get_feed_sequence_cache()
        assert cache.get_feed_sequence_cache() is sequences
        assert sequences is not current and sequences is not sitemap
        assert sequences.namespace == current.namespace
        assert options[0]["socket_timeout"] == 0.2
        assert options[2]["socket_connect_timeout"] == options[2]["socket_timeout"] == 1
        assert options[2]["retry"].get_retries() == 0
        assert options[2]["max_connections"] == 32
        cache.close_cache()
        assert cache._cache_for_process.cache_info().currsize == 0
        assert len(connections) == 3
        for connection in connections:
            connection.close.assert_called_once()
    finally:
        cache._cache_for_process.cache_clear()


def test_shutdown_does_not_allocate_unused_feed_or_sitemap_pools(monkeypatch):
    connect = Mock(side_effect=lambda *_args, **_kwargs: Mock())
    monkeypatch.setattr(cache, "create_redis", connect)
    cache._cache_for_process.cache_clear()
    try:
        cache.close_cache()
        connect.assert_not_called()
        original = cache.get_cache()
        assert original.feed_sequences is None and original.sitemap is None
        cache.close_cache()
        connect.assert_called_once()
        original.redis.close.assert_called_once()
        assert cache._cache_for_process.cache_info().currsize == 0
        replacement = cache.get_cache()
        assert replacement is not original and replacement.redis is not original.redis
        replacement.redis.close.assert_not_called()
        assert connect.call_count == 2
    finally:
        cache.close_cache()


@pytest.mark.parametrize("failing_pool", ["response", "sitemap", "feed_sequences"])
def test_shutdown_closes_every_owned_pool_and_forgets_closed_clients_after_failure(
    monkeypatch, failing_pool
):
    connect = Mock(side_effect=lambda *_args, **_kwargs: Mock())
    monkeypatch.setattr(cache, "create_redis", connect)
    cache._cache_for_process.cache_clear()
    try:
        original = cache.get_cache()
        pools = {
            "response": original,
            "sitemap": cache.get_sitemap_cache(),
            "feed_sequences": cache.get_feed_sequence_cache(),
        }
        pools[failing_pool].redis.close.side_effect = RuntimeError("Transport close failed")
        with pytest.raises(RuntimeError, match="Transport close failed"):
            cache.close_cache()
        for pool in pools.values():
            pool.redis.close.assert_called_once()
        assert cache._cache_for_process.cache_info().currsize == 0
        replacement = cache.get_cache()
        assert replacement is not original and replacement.redis is not original.redis
        assert replacement.sitemap is None and replacement.feed_sequences is None
        replacement.redis.close.assert_not_called()
    finally:
        cache.close_cache()


def test_retention_removes_selected_payloads_and_step_outputs_but_preserves_audit_identity():
    from datetime import timedelta

    stamp = NOW - timedelta(days=40)
    current = proposal(
        status="rejected",
        created_at=stamp - timedelta(minutes=1),
        reviewed_at=stamp + timedelta(minutes=2),
        reviewed_by=ACTOR,
        review_note="Research could not establish sufficient primary evidence.",
    )
    draft = copy.deepcopy(current.proposed)
    document = analysis.source_snapshot(
        Article(
            canonical_url="https://publisher.example/about",
            title="About the publisher",
            summary="About this publication and its editorial contact information.",
            metadata_source_type="publisher",
        ),
        None,
    )
    negative = analysis.AnalysisResult(
        outcome="insufficient_evidence",
        developer_relevance="uncertain",
        page_kind="uncertain",
        language=None,
        content_type=None,
        content_format=None,
        ai_title=None,
        title_evidence=None,
        ai_summary=None,
        ai_description=None,
        topics=[],
        tags=[],
        reasons=["This is an About page"],
    ).model_dump(mode="json")
    step_digest = prepare_article_step(
        "developer_relevance", document, {"topics": [], "tags": []}, {}
    )[2]
    output = RelevanceResult(
        outcome="insufficient_evidence",
        developer_relevance="uncertain",
        reason="The page does not establish an editorial subject.",
    )
    article = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=uuid.uuid4(),
        status="succeeded",
        outcome="insufficient_evidence",
        input_snapshot=document,
        input_hash=analysis.snapshot_hash(document),
        catalog_snapshot={"topics": [], "tags": []},
        usage={"inputTokens": 100, "attempts": {"1": {"tokens": {"inputTokens": 100}}}},
        result=negative,
        attempts=1,
        created_at=stamp,
        available_at=stamp,
        finished_at=stamp + timedelta(minutes=1),
        steps={
            "developer_relevance": {
                "status": "succeeded",
                "input_hash": step_digest,
                "output": output.model_dump(mode="json"),
                "tokens": {"inputTokens": 100},
            },
            "legacy-corrupt-step": None,
        },
    )
    # This selected historical row belongs to a reviewed proposal; eligibility
    # and preservation of its newer/current jobs are database integration proofs.
    research = TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=current.id,
        status="succeeded",
        outcome="insufficient_evidence",
        input_snapshot={"topic": draft, "evidence": []},
        input_hash=analysis.snapshot_hash(draft),
        result=TopicResearchResult(
            outcome="insufficient_evidence",
            kind=None,
            description=None,
            aliases=[],
            keywords=[],
            website_url=None,
            logo_url=None,
            facts=[],
            sources=[],
            reasons=["Primary evidence unavailable"],
        ).model_dump(mode="json"),
        usage={"inputTokens": 50},
        attempts=1,
        created_at=stamp,
        available_at=stamp,
        finished_at=stamp + timedelta(minutes=1),
    )
    identities = [
        (row.id, row.input_hash, row.result.copy(), row.status, row.finished_at)
        for row in (article, research)
    ]
    for row in (article, research):
        job_retention._prune_payload(row, NOW)
    for row, before in zip((article, research), identities, strict=True):
        assert (row.id, row.input_hash, row.result, row.status, row.finished_at) == before
        assert row.input_snapshot == {} and row.inputs_pruned_at == NOW
        assert row.usage["input_payload_pruned_at"] == NOW.isoformat()
    assert article.catalog_snapshot == {}
    assert article.steps == {
        "developer_relevance": {
            "status": "succeeded",
            "input_hash": step_digest,
            "tokens": {"inputTokens": 100},
        }
    }
    assert article.usage["attempts"] == {"1": {"tokens": {"inputTokens": 100}}}


def test_shared_queue_merges_preselected_lanes_by_due_created_and_stable_id():
    from datetime import timedelta

    now = NOW
    early, late, lost = (uuid.UUID(int=value) for value in (11, 12, 13))
    due = now - timedelta(minutes=2)
    created = now - timedelta(hours=1)
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.execute.side_effect = [
        [(late, due, created), (lost, due, created)],
        [(early, due, created)],
    ]
    # The second selected identity became unavailable before its row lock.
    job = ArticleEnrichmentJob(id=early, article_id=uuid.uuid4(), available_at=due, attempts=0)
    session.scalars.return_value.all.side_effect = [[job], []]
    lanes = [DispatchLane("analysis"), DispatchLane("article-enrichment")]
    queue = Mock()
    counts = job_dispatch.dispatch_lanes(factory, queue, 2, now, lanes)
    assert counts == {"analysis": 0, "article-enrichment": 1}
    assert [call.args[1] for call in queue.enqueue.call_args_list] == [str(early)]
    assert factory.begin.call_count == 1
    assert session.scalars.call_count == 2
    queries = [str(call.args[0]) for call in session.scalars.call_args_list]
    assert all("FOR UPDATE" in query for query in queries)
    assert job.dispatched_at == now
