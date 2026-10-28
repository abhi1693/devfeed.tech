"""Migration planning and Redis protocol checks; Lua atomicity uses integration tests."""

import json
import zlib
from unittest.mock import MagicMock, call

import pytest
from devfeed_aggregator import queue_migration as migration
from devfeed_core.config import get_settings
from devfeed_core.job_definitions import JOB_DEFINITIONS
from redis.exceptions import ResponseError
from rq.serializers import JSONSerializer

INGEST = "devfeed_aggregator.tasks.ingest"
PARTNER = "devfeed_aggregator.partner_sync_tasks.process_pipeline"


def payload(handler=INGEST, *, compressed=False):
    data = JSONSerializer.dumps([handler, None, ["job-123"], {}])
    return zlib.compress(data) if compressed else data


def record(source="ingestion", *, handler=INGEST, metadata=None):
    return [b"queued", source.encode(), payload(handler), metadata]


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(get_settings(), "job_queue_overrides", {})
    connection = MagicMock()
    connection.smembers.return_value = set()
    connection.sismember.return_value = False
    connection.scan_iter.return_value = []
    connection.lrange.return_value = []
    connection.llen.return_value = 0
    connection.zcard.return_value = 0
    connection.eval.return_value = b"ok"
    return connection


@pytest.mark.parametrize("compressed", [False, True])
def test_payload_accepts_only_the_known_json_job_contract(compressed):
    assert migration._payload(payload(compressed=compressed)) == (INGEST, {})


@pytest.mark.parametrize(
    "document",
    [
        [42, None, ["id"], {}],
        ["unrelated.handler", None, ["id"], {}],
        [INGEST, "bound-instance", ["id"], {}],
        [INGEST, None, "id", {}],
        [INGEST, None, [], {}],
        [INGEST, None, ["first", "second"], {}],
        [INGEST, None, [42], {}],
        [INGEST, None, ["id"], []],
    ],
)
def test_payload_rejects_unknown_handlers_and_non_job_arguments(document):
    with pytest.raises(ValueError, match="unsupported_payload"):
        migration._payload(JSONSerializer.dumps(document))


@pytest.mark.parametrize("raw", [b"not-json", b"\xff", b"[]", b"null"])
def test_payload_rejects_malformed_json(raw):
    with pytest.raises(ValueError, match="malformed_payload"):
        migration._payload(raw)


def test_payload_bounds_raw_and_inflated_data_and_rejects_compression_trailers():
    with pytest.raises(ValueError, match="payload_too_large"):
        migration._payload(b"x" * (migration.MAX_PAYLOAD_BYTES + 1))
    for raw in [
        zlib.compress(b"x" * (migration.MAX_PAYLOAD_BYTES + 1)),
        zlib.compress(payload())[:-1],
        zlib.compress(payload()) + b"unexpected-trailer",
    ]:
        with pytest.raises(ValueError, match="malformed_payload"):
            migration._payload(raw)


@pytest.mark.parametrize(
    "source,handler,meta,logical,ai",
    [
        ("ingestion", INGEST, None, "ingestion", None),
        (
            "article-analysis-fresh",
            JOB_DEFINITIONS["analysis"].handler,
            None,
            "article-analysis-fresh",
            None,
        ),
        (
            "ingestion",
            "devfeed_aggregator.recommendation_tasks.refresh",
            None,
            "recommendations",
            None,
        ),
        (
            "source-analysis",
            "devfeed_aggregator.partner_tasks.process_evaluation",
            None,
            "partner-evaluation",
            True,
        ),
        ("ingestion", PARTNER, None, "partner-pipeline", False),
        ("source-discovery", PARTNER, None, "partner-pipeline", False),
        ("source-analysis", PARTNER, None, "partner-pipeline", True),
        ("dedicated", PARTNER, b'{"devfeed_ai":true}', "partner-pipeline", True),
    ],
)
def test_plan_preserves_payload_and_assigns_logical_admission_identity(
    transport, source, handler, meta, logical, ai
):
    fields = record(source, handler=handler, metadata=meta)
    plan = migration._plan(source, "delivery", fields)
    replacement = json.loads(plan.replacement)
    assert plan.payload == fields[2] and plan.metadata == fields[3]
    assert replacement["devfeed_queue"] == logical
    assert replacement.get("devfeed_ai") is ai
    assert plan.identifier == "delivery" and plan.source == source


def test_plan_accepts_decoded_redis_fields_and_preserves_diagnostic_metadata(transport):
    fields = ["queued", "ingestion", payload().decode(), '{"trace":"kept"}']
    plan = migration._plan("ingestion", "delivery", fields)
    assert plan.destination == "default"
    assert json.loads(plan.replacement) == {"trace": "kept", "devfeed_queue": "ingestion"}


@pytest.mark.parametrize(
    "index,value,reason",
    [
        (0, b"started", "job_not_queued"),
        (1, b"different", "origin_mismatch"),
        (2, None, "missing_payload"),
        (3, b"not-json", "malformed_metadata"),
        (3, b"[]", "malformed_metadata"),
        (3, b'{"devfeed_queue":42}', "unknown_logical_queue"),
        (3, b'{"devfeed_queue":"unknown"}', "unknown_logical_queue"),
        (3, b'{"devfeed_queue":"article-analysis"}', "handler_logical_mismatch"),
    ],
)
def test_plan_rejects_ambiguous_or_changed_records(transport, index, value, reason):
    fields = record()
    fields[index] = value
    with pytest.raises(ValueError, match=reason):
        migration._plan("ingestion", "delivery", fields)


def test_plan_does_not_infer_a_kind_from_a_shared_or_unknown_source(transport):
    with pytest.raises(ValueError, match="unknown_logical_queue"):
        migration._plan("default", "delivery", record("default"))
    with pytest.raises(ValueError, match="ambiguous_partner_stage"):
        migration._plan("custom", "delivery", record("custom", handler=PARTNER))
    with pytest.raises(ValueError, match="partner_admission_mismatch"):
        migration._plan(
            "source-analysis",
            "delivery",
            record("source-analysis", handler=PARTNER, metadata=b'{"devfeed_ai":false}'),
        )


def test_guards_ignore_dead_expired_workers_and_report_live_and_inflight(transport):
    transport.smembers.return_value = {b"rq:worker:live", "rq:worker:dead", b"rq:worker:expired"}
    transport.exists.side_effect = lambda key: key != b"rq:worker:expired"
    transport.hget.side_effect = lambda key, field: (
        b"yesterday" if key == "rq:worker:dead" else None
    )
    transport.llen.return_value = 2
    transport.zcard.return_value = 3
    workers, inflight = migration._guards(transport, ("ingestion",))
    assert workers == ["rq:worker:live"]
    assert inflight == {"ingestion": {"intermediate": 2, "started": 3}}


@pytest.mark.parametrize(
    "problem,expected",
    [
        ("dependency-set", []),
        ("hash-type-race", ["job_hash_changed"]),
        ("finished", []),
        ("missing-origin", []),
        ("invalid-encoding", []),
        ("other-source", []),
        ("intermediate", []),
        ("orphan", ["orphaned_queued_delivery"]),
        ("listed", []),
        ("unmigrated-listed", ["unmigrated_delivery_remains"]),
        ("invalid-listed", ["malformed_payload"]),
    ],
)
def test_orphan_observation_never_mistakes_dependencies_or_owned_claims_for_lost_work(
    transport, problem, expected
):
    transport.scan_iter.return_value = [b"rq:job:delivery"]
    transport.type.return_value = b"hash"
    fields = record()
    transport.hmget.return_value = fields
    transport.lpos.return_value = None
    verify = problem in {"unmigrated-listed", "invalid-listed"}
    if problem == "dependency-set":
        transport.type.return_value = b"set"
    elif problem == "hash-type-race":
        transport.hmget.side_effect = ResponseError("WRONGTYPE")
    elif problem == "finished":
        fields[0] = b"finished"
    elif problem == "missing-origin":
        fields[1] = None
    elif problem == "invalid-encoding":
        fields[1] = b"\xff"
    elif problem == "other-source":
        fields[1] = b"other"
    elif problem == "intermediate":
        transport.lpos.side_effect = [None, 0]
    elif problem in {"listed", "unmigrated-listed", "invalid-listed"}:
        transport.lpos.return_value = 0
        if problem == "invalid-listed":
            fields[2] = b"broken"
    assert [
        row["reason"]
        for row in migration._orphans(transport, ("ingestion",), verify_applied=verify)
    ] == expected


def test_applied_observation_accepts_current_canonical_metadata_with_decoded_keys(transport):
    transport.scan_iter.return_value = ["rq:job:delivery"]
    transport.type.return_value = "hash"
    transport.hmget.return_value = record("default", metadata=b'{"devfeed_queue": "ingestion"}')
    transport.lpos.return_value = 0
    assert migration._orphans(transport, ("default",), verify_applied=True) == []


def prepared_batch(connection, *, source="ingestion", identifiers=None, rows=None):
    identifiers = [b"first", b"second"] if identifiers is None else identifiers
    rows = [record(source) for _ in identifiers] if rows is None else rows
    connection.lrange.side_effect = [identifiers, []]
    pipe = connection.pipeline.return_value.__enter__.return_value
    pipe.execute.return_value = rows
    return pipe


def test_dry_run_reads_bounded_pages_and_never_calls_mutating_protocol(transport):
    pipe = prepared_batch(transport)
    result = migration.migrate_queues(transport, sources=["ingestion"])
    assert result["ready"] and not result["applied"] and not result["complete"]
    assert result["scanned"] == result["would_move"] == 2
    assert transport.lrange.call_args_list == [
        call("rq:queue:ingestion", 0, 99),
        call("rq:queue:ingestion", 2, 101),
    ]
    pipe.execute.assert_called_once_with(raise_on_error=False)
    transport.eval.assert_not_called()


@pytest.mark.parametrize("response", [b"ok", "ok", b"worker_active"])
def test_apply_forwards_unchanged_snapshots_to_atomic_boundary_and_checks_response(
    transport, response
):
    prepared_batch(transport)
    transport.eval.return_value = response
    result = migration.migrate_queues(transport, apply=True, sources=["ingestion"])
    assert result["complete"] is (response != b"worker_active")
    args = transport.eval.call_args_list[0].args
    assert args[:2] == (migration.MOVE_DELIVERY, 7)
    assert args[2:9] == (
        "rq:queue:ingestion",
        "rq:queue:default",
        "rq:queue:ingestion:intermediate",
        "rq:wip:ingestion",
        "rq:job:first",
        "rq:workers",
        "rq:queues",
    )
    assert args[9:15] == ("first", "ingestion", "default", payload(), b"", 0)
    assert json.loads(args[15]) == {"devfeed_queue": "ingestion"}
    if response == b"worker_active":
        assert result["blocked"] == [{"source": "ingestion", "reason": "worker_active"}]
        assert transport.eval.call_count == 1


def test_apply_keeps_already_target_jobs_in_place_and_counts_annotation(transport):
    fields = record("default", metadata=b'{"devfeed_queue":"ingestion"}')
    prepared_batch(transport, source="default", identifiers=["delivery"], rows=[fields])
    result = migration.migrate_queues(transport, apply=True, sources=["default"])
    assert result["complete"] and result["moved"] == 0 and result["annotated"] == 1
    args = transport.eval.call_args.args
    assert args[2] == args[3] == "rq:queue:default" and args[-2] == 1


@pytest.mark.parametrize("issue", ["duplicate", "wrongtype", "bad-encoding", "active-worker"])
def test_preflight_blocks_all_publication_when_any_source_is_unsafe(transport, issue):
    identifiers, rows = [b"first", b"second"], [record(), record()]
    if issue == "duplicate":
        identifiers[1] = identifiers[0]
    elif issue == "wrongtype":
        rows[1] = ResponseError("WRONGTYPE")
    elif issue == "bad-encoding":
        identifiers[1] = b"\xff"
    else:
        transport.smembers.return_value = {b"rq:worker:live"}
        transport.exists.return_value = True
        transport.hget.return_value = None
    prepared_batch(transport, identifiers=identifiers, rows=rows)
    result = migration.migrate_queues(transport, apply=True, sources=["ingestion"])
    assert not result["ready"] and not result["applied"] and not result["complete"]
    transport.eval.assert_not_called()


def test_source_selection_rejects_unknown_queues_and_accepts_registered_custom_destinations(
    transport,
):
    with pytest.raises(ValueError, match="Unknown migration source"):
        migration.migrate_queues(transport, sources=["unregistered"])
    transport.sismember.return_value = True
    result = migration.migrate_queues(transport, sources=["old-custom"])
    assert result["ready"] and result["source_queues_remaining"] == {"old-custom": 0}
    assert migration.migrate_queues(transport, sources=None)["ready"]
