"""Offline queue upgrades preserve real Redis/RQ transport state and admission."""

import os
import uuid
from urllib.parse import urlsplit

import pytest
from devfeed_aggregator import queue_migration
from devfeed_core.config import get_settings
from redis import Redis
from rq import Queue, Retry
from rq.serializers import JSONSerializer

pytestmark = pytest.mark.integration
HANDLER = "devfeed_aggregator.tasks.ingest"


@pytest.fixture
def transport(monkeypatch):
    url = os.environ.get("DEVFEED_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_REDIS_URL for disposable Redis migration tests")
    if urlsplit(url).path != "/15":
        pytest.fail("Migration tests flush disposable Redis database 15")
    monkeypatch.setattr(get_settings(), "job_queue_overrides", {})
    with Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3) as connection:
        connection.flushdb()
        yield connection
        connection.flushdb()


def enqueue(connection, source="ingestion", *, handler=HANDLER, identifier=None, meta=None):
    return Queue(source, connection=connection, serializer=JSONSerializer).enqueue(
        handler,
        str(uuid.uuid4()),
        job_id=identifier,
        meta=meta,
        retry=Retry(max=3, interval=[10, 30, 60]),
        result_ttl=321,
        failure_ttl=654,
        job_timeout=45,
    )


def test_dry_run_previews_routes_without_mutation(transport):
    first = enqueue(transport, identifier="first")
    before = transport.hgetall(first.key)
    result = queue_migration.migrate_queues(transport)
    assert result["ready"] and not result["applied"] and not result["complete"]
    assert result["would_move"] == 1 and result["moved"] == 0
    assert result["destinations"] == {"ingestion": {"default": 1}}
    assert transport.hgetall(first.key) == before
    assert transport.lrange("rq:queue:ingestion", 0, -1) == [b"first"]
    assert not transport.exists("rq:queue:default")


def test_apply_preserves_order_payload_retry_fields_and_ttl(transport):
    existing = enqueue(
        transport, "default", identifier="target", meta={"devfeed_queue": "ingestion"}
    )
    jobs = [enqueue(transport, identifier=f"old-{i}", meta={"trace": "retained"}) for i in range(3)]
    hashes = {job.id: transport.hgetall(job.key) for job in jobs}
    for job in jobs:
        transport.pexpire(job.key, 900_000)
    before = {job.id: transport.pttl(job.key) for job in jobs}
    result = queue_migration.migrate_queues(transport, apply=True)
    assert result["complete"] and result["moved"] == 3
    assert result["source_queues_remaining"]["ingestion"] == 0
    assert transport.lrange("rq:queue:default", 0, -1) == [
        existing.id.encode(),
        b"old-0",
        b"old-1",
        b"old-2",
    ]
    for job in jobs:
        current = transport.hgetall(job.key)
        assert {
            key: value for key, value in current.items() if key not in {b"origin", b"meta"}
        } == {
            key: value for key, value in hashes[job.id].items() if key not in {b"origin", b"meta"}
        }
        assert current[b"origin"] == b"default"
        assert JSONSerializer.loads(current[b"meta"]) == {
            "trace": "retained",
            "devfeed_queue": "ingestion",
        }
        assert 0 < transport.pttl(job.key) <= before[job.id]
        assert before[job.id] - transport.pttl(job.key) < 2000
    assert transport.sismember("rq:queues", "rq:queue:default")
    assert not transport.sismember("rq:queues", "rq:queue:ingestion")
    assert queue_migration.migrate_queues(transport, apply=True)["moved"] == 0


def test_aliases_route_recommendations_and_partner_assessment_with_admission_metadata(transport):
    rec = enqueue(transport, handler="devfeed_aggregator.recommendation_tasks.refresh")
    ai = enqueue(
        transport,
        "source-analysis",
        handler="devfeed_aggregator.partner_sync_tasks.process_pipeline",
    )
    io = enqueue(transport, handler="devfeed_aggregator.partner_sync_tasks.process_pipeline")
    evaluation = enqueue(
        transport, "source-analysis", handler="devfeed_aggregator.partner_tasks.process_evaluation"
    )
    result = queue_migration.migrate_queues(transport, apply=True)
    assert result["complete"] and result["moved"] == 4
    assert transport.hget(rec.key, "origin") == b"high"
    assert (
        JSONSerializer.loads(transport.hget(rec.key, "meta"))["devfeed_queue"] == "recommendations"
    )
    assert JSONSerializer.loads(transport.hget(ai.key, "meta"))["devfeed_ai"] is True
    assert JSONSerializer.loads(transport.hget(io.key, "meta"))["devfeed_ai"] is False
    assert JSONSerializer.loads(transport.hget(evaluation.key, "meta"))["devfeed_ai"] is True


@pytest.mark.parametrize("kind", ["worker", "intermediate", "started"])
def test_apply_refuses_live_workers_and_inflight_deliveries(transport, kind):
    job = enqueue(transport)
    if kind == "worker":
        transport.sadd("rq:workers", "rq:worker:live")
        transport.hset("rq:worker:live", mapping={"state": "idle"})
        transport.expire("rq:worker:live", 90)
    elif kind == "intermediate":
        transport.rpush("rq:queue:ingestion:intermediate", "other")
    else:
        transport.zadd("rq:wip:ingestion", {"other": 1})
    result = queue_migration.migrate_queues(transport, apply=True)
    assert not result["applied"] and not result["ready"] and not result["complete"]
    assert transport.hget(job.key, "origin") == b"ingestion"
    assert result["active_workers"] if kind == "worker" else result["inflight"]


@pytest.mark.parametrize(
    "problem", ["payload", "metadata", "status", "origin", "duplicate", "orphan", "unknown-handler"]
)
def test_invalid_deliveries_block_the_whole_apply_without_silent_loss(transport, problem):
    good = enqueue(transport, identifier="good")
    job = enqueue(transport, identifier="bad")
    if problem == "payload":
        transport.hset(job.key, "data", b"private-malformed-payload")
    elif problem == "metadata":
        transport.hset(job.key, "meta", b"[]")
    elif problem == "status":
        transport.hset(job.key, "status", b"started")
    elif problem == "origin":
        transport.hset(job.key, "origin", b"unrelated")
    elif problem == "duplicate":
        transport.rpush("rq:queue:ingestion", job.id)
    elif problem == "orphan":
        transport.lrem("rq:queue:ingestion", 1, job.id)
    else:
        transport.hset(job.key, "data", JSONSerializer.dumps(["unknown.call", None, ["id"], {}]))
    result = queue_migration.migrate_queues(transport, apply=True)
    assert not result["applied"] and not result["complete"] and result["blocked"]
    assert transport.hget(good.key, "origin") == b"ingestion"
    assert "private-malformed-payload" not in str(result)


def test_explicit_current_destination_is_annotated_without_reordering(transport):
    job = enqueue(transport, "default", meta={"devfeed_queue": "ingestion"})
    before = transport.hgetall(job.key)
    result = queue_migration.migrate_queues(transport, apply=True, sources=["default"])
    assert result["complete"] and result["moved"] == 0 and result["already_target"] == 1
    assert transport.hgetall(job.key) == before
    assert transport.lrange("rq:queue:default", 0, -1) == [job.id.encode()]


@pytest.mark.parametrize("race", ["worker", "metadata", "claim", "destination"])
def test_atomic_claim_rechecks_worker_and_delivery_races(transport, monkeypatch, race):
    job = enqueue(transport)
    original = transport.eval

    def interleaved(script, *args):
        if race == "worker":
            transport.sadd("rq:workers", "rq:worker:new")
            transport.hset("rq:worker:new", mapping={"state": "busy"})
        elif race == "metadata":
            transport.hset(job.key, "meta", b'{"changed":true}')
        elif race == "claim":
            transport.lmove(
                "rq:queue:ingestion", "rq:queue:ingestion:intermediate", "LEFT", "RIGHT"
            )
        else:
            transport.rpush("rq:queue:default", job.id)
        return original(script, *args)

    monkeypatch.setattr(transport, "eval", interleaved)
    result = queue_migration.migrate_queues(transport, apply=True)
    assert not result["complete"] and result["moved"] == 0 and result["blocked"]
    assert transport.hget(job.key, "origin") == b"ingestion"
    assert transport.lrange("rq:queue:default", 0, -1).count(job.id.encode()) <= 1


def test_unknown_sources_are_rejected_before_scan(transport):
    with pytest.raises(ValueError, match="Unknown migration source"):
        queue_migration.migrate_queues(transport, apply=True, sources=["another-app"])


@pytest.mark.parametrize(
    "kind",
    [
        "oversized",
        "compressed-bomb",
        "missing",
        "bad-meta-json",
        "unknown-kind",
        "shared-ambiguous",
        "partner-ambiguous",
    ],
)
def test_unsafe_payloads_and_ambiguous_routes_are_reported_without_mutation(transport, kind):
    import zlib

    source = "default" if kind in {"shared-ambiguous", "partner-ambiguous"} else "ingestion"
    job = enqueue(transport, source)
    if kind == "oversized":
        transport.hset(job.key, "data", b"x" * (queue_migration.MAX_PAYLOAD_BYTES + 1))
    elif kind == "compressed-bomb":
        transport.hset(
            job.key, "data", zlib.compress(b"x" * (queue_migration.MAX_PAYLOAD_BYTES + 1))
        )
    elif kind == "missing":
        transport.hdel(job.key, "data")
    elif kind == "bad-meta-json":
        transport.hset(job.key, "meta", b"not-json")
    elif kind == "unknown-kind":
        transport.hset(job.key, "meta", b'{"devfeed_queue":"unknown"}')
    elif kind == "partner-ambiguous":
        transport.hset(
            job.key,
            "data",
            JSONSerializer.dumps(
                ["devfeed_aggregator.partner_sync_tasks.process_pipeline", None, ["id"], {}]
            ),
        )
    result = queue_migration.migrate_queues(transport, apply=True, sources=[source])
    assert not result["applied"] and result["blocked"] and not result["complete"]
    assert transport.hget(job.key, "origin") == source.encode()


def test_existing_partner_hint_and_older_discovery_sync_route_keep_admission(transport):
    current = enqueue(
        transport,
        "default",
        handler="devfeed_aggregator.partner_sync_tasks.process_pipeline",
        meta={"devfeed_queue": "partner-pipeline", "devfeed_ai": False},
    )
    old = enqueue(
        transport,
        "source-discovery",
        handler="devfeed_aggregator.partner_sync_tasks.process_pipeline",
    )
    result = queue_migration.migrate_queues(
        transport, apply=True, sources=["default", "source-discovery"]
    )
    assert result["complete"] and result["moved"] == 1
    for job in (current, old):
        assert JSONSerializer.loads(transport.hget(job.key, "meta"))["devfeed_ai"] is False


def test_expired_dead_worker_memberships_and_unrelated_hashes_do_not_block(transport):
    enqueue(transport)
    transport.sadd("rq:workers", "rq:worker:expired", "rq:worker:dead")
    transport.hset("rq:worker:dead", mapping={"death": "2026-10-28T00:00:00Z"})
    transport.hset("rq:job:foreign", mapping={"status": "queued", "origin": "another-app"})
    transport.hset("rq:job:malformed-origin", mapping={"status": "queued", "origin": b"\xff"})
    assert queue_migration.migrate_queues(transport, apply=True)["complete"]


def test_unchanged_image_destination_is_annotated_by_default(transport):
    job = enqueue(transport, "images", handler="devfeed_aggregator.image_tasks.enrich_image")
    result = queue_migration.migrate_queues(transport, apply=True)
    assert result["complete"] and result["moved"] == 0 and result["annotated"] == 1
    assert JSONSerializer.loads(transport.hget(job.key, "meta"))["devfeed_queue"] == "images"
    assert transport.lrange("rq:queue:images", 0, -1) == [job.id.encode()]


def test_new_publisher_delivery_after_preflight_prevents_false_completion(transport, monkeypatch):
    enqueue(transport)
    original = transport.eval

    def interleaved(script, *args):
        result = original(script, *args)
        enqueue(transport, identifier="late-publisher")
        return result

    monkeypatch.setattr(transport, "eval", interleaved)
    result = queue_migration.migrate_queues(transport, apply=True)
    assert result["applied"] and result["moved"] == 1 and not result["complete"]
    assert result["source_queues_remaining"]["ingestion"] == 1
    assert result["blocked"][0]["reason"] == "unmigrated_delivery_remains"


def test_dependency_sets_matching_job_scan_are_not_treated_as_hashes(transport):
    job = enqueue(transport)
    transport.sadd(job.key + ":dependencies", "other-job")
    transport.sadd(job.key + ":dependents", "dependent-job")
    result = queue_migration.migrate_queues(transport, apply=True)
    assert result["complete"] and result["moved"] == 1
    assert transport.smembers(job.key + ":dependencies") == {b"other-job"}
    assert transport.smembers(job.key + ":dependents") == {b"dependent-job"}


def test_wrong_type_job_key_is_reported_as_a_blocker(transport):
    job = enqueue(transport)
    transport.delete(job.key)
    transport.set(job.key, "malformed-private-data")
    result = queue_migration.migrate_queues(transport, apply=True)
    assert not result["applied"] and not result["complete"]
    assert result["blocked"][0]["reason"] == "malformed_job_hash"
    assert "malformed-private-data" not in str(result)


def test_hash_type_change_during_orphan_scan_is_reported_without_mutation(transport, monkeypatch):
    job = enqueue(transport)
    original = transport.hmget

    def interleaved(key, *args):
        if args == ("status", "origin", "data", "meta"):
            transport.delete(key)
            transport.sadd(key, "racing-change")
        return original(key, *args)

    monkeypatch.setattr(transport, "hmget", interleaved)
    result = queue_migration.migrate_queues(transport, apply=True)
    assert not result["applied"] and not result["complete"]
    assert result["blocked"][0]["reason"] == "job_hash_changed"
    assert transport.lrange("rq:queue:ingestion", 0, -1) == [job.id.encode()]


@pytest.mark.parametrize("metadata", [None, {"devfeed_queue": "ingestion"}])
def test_known_ai_handler_cannot_migrate_as_non_ai_kind(transport, metadata):
    job = enqueue(
        transport,
        "ingestion",
        handler="devfeed_aggregator.analysis_tasks.analyze_article",
        meta=metadata,
    )
    result = queue_migration.migrate_queues(transport, apply=True)
    assert not result["applied"] and not result["complete"]
    assert result["blocked"][0]["reason"] == "handler_logical_mismatch"
    assert transport.hget(job.key, "origin") == b"ingestion"


def test_partner_assessment_source_cannot_migrate_with_non_ai_hint(transport):
    job = enqueue(
        transport,
        "source-analysis",
        handler="devfeed_aggregator.partner_sync_tasks.process_pipeline",
        meta={"devfeed_ai": False},
    )
    result = queue_migration.migrate_queues(transport, apply=True)
    assert not result["applied"] and result["blocked"][0]["reason"] == "partner_admission_mismatch"
    assert transport.hget(job.key, "origin") == b"source-analysis"


def test_registered_former_override_migrates_only_with_known_logical_metadata(transport):
    job = enqueue(transport, "former-pipeline", meta={"devfeed_queue": "ingestion"})
    result = queue_migration.migrate_queues(transport, apply=True, sources=["former-pipeline"])
    assert result["complete"] and result["moved"] == 1
    assert transport.hget(job.key, "origin") == b"default"
