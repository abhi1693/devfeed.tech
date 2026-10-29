"""Explicit, offline upgrade of queued RQ deliveries into configured transports.

Stop publishers and workers before applying. Dry runs never mutate Redis. Runtime
workers do not depend on this one-time tool or retain old routing behavior.
"""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass
from typing import Any

from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.worker_queues import (
    AI_QUEUES,
    QUEUES,
    ROUTABLE_QUEUES,
    configured_queue,
    physical_queues,
)
from redis import Redis
from redis.exceptions import ResponseError

BATCH_SIZE = 100
MAX_PAYLOAD_BYTES = 1_048_576
ALIASES = {
    "devfeed_aggregator.recommendation_tasks.refresh": "recommendations",
    "devfeed_aggregator.partner_tasks.process_evaluation": "partner-evaluation",
    "devfeed_aggregator.partner_sync_tasks.process_pipeline": "partner-pipeline",
}
HANDLER_LANES = {definition.handler: {definition.queue} for definition in JOB_DEFINITIONS.values()}
for definition in JOB_DEFINITIONS.values():
    if definition.kind in {"analysis", "article-enrichment"}:
        HANDLER_LANES[definition.handler].add(definition.queue + "-fresh")
    if definition.kind == "topic-analysis":
        HANDLER_LANES[definition.handler].add("relationships")
    if definition.kind == "source-enrichment":
        HANDLER_LANES[definition.handler].add("source-analysis")
    if definition.supports_solver:
        HANDLER_LANES[definition.handler].add("solver")
HANDLER_LANES.update({handler: {logical} for handler, logical in ALIASES.items()})
HANDLER_LANES["devfeed_aggregator.discovery_tasks.process_candidate"] = {
    "source-discovery",
    "source-analysis",
}
HANDLERS = set(HANDLER_LANES)


# Each move is atomic with respect to registration, claims, metadata and TTL expiry.
# HSET preserves all delivery fields and the hash's remaining TTL. The original
# serialized payload is compared but never rewritten or deserialized as pickle.
MOVE_DELIVERY = """
for _, worker in ipairs(redis.call('SMEMBERS', KEYS[6])) do
    local death = redis.call('HGET', worker, 'death')
    if redis.call('EXISTS', worker) == 1 and (not death or death == '') then
        return 'worker_active'
    end
end
if redis.call('LLEN', KEYS[3]) > 0 or redis.call('ZCARD', KEYS[4]) > 0 then
    return 'source_inflight'
end
if redis.call('HGET', KEYS[5], 'status') ~= 'queued' then return 'job_not_queued' end
if redis.call('HGET', KEYS[5], 'origin') ~= ARGV[2] then return 'origin_changed' end
if redis.call('HGET', KEYS[5], 'data') ~= ARGV[4] then return 'payload_changed' end
local meta = redis.call('HGET', KEYS[5], 'meta')
if ARGV[6] == '1' then
    if meta ~= ARGV[5] then return 'metadata_changed' end
elseif meta then
    return 'metadata_changed'
end
local positions = redis.call('LPOS', KEYS[1], ARGV[1], 'COUNT', 2)
if #positions ~= 1 then return 'source_membership_changed' end
if KEYS[1] ~= KEYS[2] and redis.call('LPOS', KEYS[2], ARGV[1]) then
    return 'duplicate_destination'
end
if KEYS[1] ~= KEYS[2] then
    redis.call('LREM', KEYS[1], 1, ARGV[1])
    redis.call('RPUSH', KEYS[2], ARGV[1])
end
redis.call('HSET', KEYS[5], 'origin', ARGV[3], 'meta', ARGV[7])
redis.call('SADD', KEYS[7], KEYS[2])
if redis.call('LLEN', KEYS[1]) == 0 then redis.call('SREM', KEYS[7], KEYS[1]) end
return 'ok'
"""


@dataclass(frozen=True)
class DeliveryMove:
    source: str
    destination: str
    identifier: str
    payload: bytes
    metadata: bytes | None
    replacement: bytes


def _payload(raw: bytes) -> tuple[str, dict[str, Any]]:
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload_too_large")
    try:
        inflater = zlib.decompressobj()
        data = inflater.decompress(raw, MAX_PAYLOAD_BYTES + 1)
        if len(data) > MAX_PAYLOAD_BYTES or not inflater.eof or inflater.unused_data:
            raise ValueError("malformed_payload")
    except zlib.error:
        data = raw
    try:
        function, instance, args, kwargs = json.loads(data)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("malformed_payload") from exc
    if (
        not isinstance(function, str)
        or function not in HANDLERS
        or instance is not None
        or not isinstance(args, list)
        or len(args) != 1
        or not isinstance(args[0], str)
        or not isinstance(kwargs, dict)
    ):
        raise ValueError("unsupported_payload")
    return function, kwargs


def _plan(source: str, identifier: str, fields: list[bytes | str | None]) -> DeliveryMove:
    status, origin, raw, old_meta = (
        value.encode() if isinstance(value, str) else value for value in fields
    )
    if status != b"queued":
        raise ValueError("job_not_queued")
    if origin != source.encode():
        raise ValueError("origin_mismatch")
    if raw is None:
        raise ValueError("missing_payload")
    function, _ = _payload(raw)
    try:
        metadata = json.loads(old_meta) if old_meta is not None else {}
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("malformed_metadata") from exc
    if not isinstance(metadata, dict):
        raise ValueError("malformed_metadata")
    logical = metadata.get("devfeed_queue")
    if logical is not None and (not isinstance(logical, str) or logical not in ROUTABLE_QUEUES):
        raise ValueError("unknown_logical_queue")
    logical = ALIASES.get(function, logical or (source if source in QUEUES else None))
    if logical not in ROUTABLE_QUEUES:
        raise ValueError("unknown_logical_queue")
    if logical not in HANDLER_LANES[function]:
        raise ValueError("handler_logical_mismatch")
    if logical == "partner-evaluation":
        metadata["devfeed_ai"] = True
    if logical == "partner-pipeline" and type(metadata.get("devfeed_ai")) is not bool:
        if source not in {"ingestion", "source-discovery", "source-analysis"}:
            raise ValueError("ambiguous_partner_stage")
        metadata["devfeed_ai"] = source in AI_QUEUES
    if logical == "partner-pipeline" and source == "source-analysis" and not metadata["devfeed_ai"]:
        raise ValueError("partner_admission_mismatch")
    metadata["devfeed_queue"] = logical
    return DeliveryMove(
        source, configured_queue(logical), identifier, raw, old_meta, json.dumps(metadata).encode()
    )


def _guards(
    connection: Redis, sources: tuple[str, ...]
) -> tuple[list[str], dict[str, dict[str, int]]]:
    workers = []
    for key in connection.smembers("rq:workers"):
        if connection.exists(key) and not connection.hget(key, "death"):
            workers.append(key.decode() if isinstance(key, bytes) else key)
    inflight = {}
    for source in sources:
        intermediate = connection.llen(f"rq:queue:{source}:intermediate")
        started = connection.zcard(f"rq:wip:{source}")
        if intermediate or started:
            inflight[source] = {"intermediate": intermediate, "started": started}
    return sorted(workers), inflight


def _orphans(
    connection: Redis, sources: tuple[str, ...], *, verify_applied: bool = False
) -> list[dict[str, str]]:
    """Detect queued hashes whose lost list pointer would otherwise hide work."""
    blocked = []
    expected = set(sources)
    for key in connection.scan_iter(match="rq:job:*", count=BATCH_SIZE):
        if connection.type(key) not in {b"hash", "hash"}:
            continue
        try:
            status, origin, payload, metadata = connection.hmget(
                key, "status", "origin", "data", "meta"
            )
        except ResponseError:
            blocked.append({"source": "unknown", "reason": "job_hash_changed"})
            continue
        if status != b"queued" or origin is None:
            continue
        try:
            source = origin.decode() if isinstance(origin, bytes) else origin
            identifier = (key.decode() if isinstance(key, bytes) else key).removeprefix("rq:job:")
        except UnicodeError:
            continue
        if source not in expected:
            continue
        if connection.lpos(f"rq:queue:{source}", identifier) is not None:
            if verify_applied:
                try:
                    plan = _plan(source, identifier, [status, origin, payload, metadata])
                    if plan.destination != source or plan.replacement != metadata:
                        raise ValueError("unmigrated_delivery_remains")
                except ValueError as exc:
                    blocked.append({"source": source, "job_id": identifier, "reason": str(exc)})
            continue
        if connection.lpos(f"rq:queue:{source}:intermediate", identifier) is not None:
            continue
        blocked.append(
            {"source": source, "job_id": identifier, "reason": "orphaned_queued_delivery"}
        )
    return blocked


def migrate_queues(
    connection: Redis, *, apply: bool = False, sources: list[str] | tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Preview or apply one offline routing upgrade, preserving delivery identity.

    Sources default to all known old named lanes, including unchanged dedicated
    destinations whose jobs still require logical metadata. An explicit source
    may also be a currently configured transport; priority queue names require
    logical metadata rather than inferring a kind from their shared destination.
    """
    destinations = set(physical_queues())
    selected = tuple(dict.fromkeys(sources if sources is not None else QUEUES))
    for source in selected:
        known = source in {*QUEUES, *destinations}
        registered = bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source)) and connection.sismember(
            "rq:queues", f"rq:queue:{source}"
        )
        if not known and not registered:
            raise ValueError("Unknown migration source queue")
    workers, inflight = _guards(connection, selected)
    summary: dict[str, Any] = {
        "applied": False,
        "ready": False,
        "complete": False,
        "scanned": 0,
        "would_move": 0,
        "moved": 0,
        "annotated": 0,
        "already_target": 0,
        "active_workers": workers,
        "inflight": inflight,
        "blocked": [],
        "destinations": {},
    }
    plans = []
    for source in selected:
        seen = set()
        offset = 0
        while identifiers := connection.lrange(
            f"rq:queue:{source}", offset, offset + BATCH_SIZE - 1
        ):
            offset += len(identifiers)
            with connection.pipeline(transaction=False) as pipe:
                for identifier in identifiers:
                    key = (
                        b"rq:job:" + identifier
                        if isinstance(identifier, bytes)
                        else "rq:job:" + identifier
                    )
                    pipe.hmget(key, "status", "origin", "data", "meta")
                rows = pipe.execute(raise_on_error=False)
            for raw_id, fields in zip(identifiers, rows, strict=True):
                summary["scanned"] += 1
                try:
                    identifier = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
                    if isinstance(fields, ResponseError):
                        raise ValueError("malformed_job_hash")
                    if identifier in seen:
                        raise ValueError("duplicate_source_delivery")
                    seen.add(identifier)
                    plan = _plan(source, identifier, fields)
                except (ValueError, UnicodeError) as exc:
                    summary["blocked"].append({"source": source, "reason": str(exc)})
                    continue
                plans.append(plan)
                routes = summary["destinations"].setdefault(source, {})
                routes[plan.destination] = routes.get(plan.destination, 0) + 1
                summary["would_move"] += plan.destination != source
                summary["already_target"] += plan.destination == source
    orphan_sources = selected if sources is not None else QUEUES
    summary["blocked"].extend(_orphans(connection, orphan_sources))
    summary["ready"] = not (workers or inflight or summary["blocked"])
    if apply and summary["ready"]:
        summary["applied"] = True
        for plan in plans:
            result = connection.eval(
                MOVE_DELIVERY,
                7,
                f"rq:queue:{plan.source}",
                f"rq:queue:{plan.destination}",
                f"rq:queue:{plan.source}:intermediate",
                f"rq:wip:{plan.source}",
                f"rq:job:{plan.identifier}",
                "rq:workers",
                "rq:queues",
                plan.identifier,
                plan.source,
                plan.destination,
                plan.payload,
                plan.metadata or b"",
                int(plan.metadata is not None),
                plan.replacement,
            )
            reason = result.decode() if isinstance(result, bytes) else result
            if reason != "ok":
                summary["blocked"].append({"source": plan.source, "reason": reason})
                break
            summary["moved"] += plan.source != plan.destination
            summary["annotated"] += plan.source == plan.destination
        summary["blocked"].extend(_orphans(connection, orphan_sources, verify_applied=True))
        workers, inflight = _guards(connection, selected)
        summary["active_workers"], summary["inflight"] = workers, inflight
        summary["ready"] = not (workers or inflight or summary["blocked"])
    summary["source_queues_remaining"] = {
        source: connection.llen(f"rq:queue:{source}") for source in selected
    }
    summary["complete"] = bool(
        apply
        and summary["ready"]
        and all(
            source in destinations or count == 0
            for source, count in summary["source_queues_remaining"].items()
        )
    )
    return summary
