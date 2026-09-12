"""Bounded operational logs, independent of worker database transactions.

Only sanitized logging records are stored. Redis persistence/eviction determines
durability; these are troubleshooting logs, not a permanent audit trail.
"""

import json
import logging
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from typing import Literal, cast, get_args
from uuid import UUID

from pydantic import BaseModel, ValidationError
from redis.backoff import NoBackoff
from redis.retry import Retry

from devfeed_core.config import get_settings
from devfeed_core.json_types import JsonValue
from devfeed_core.log_text import error_text, event_text
from devfeed_core.logging import JsonFormatter, elapsed_ms, log_context
from devfeed_core.redis import create_redis
from redis import Redis

JobKind = Literal[
    "ingestion",
    "article-enrichment",
    "images",
    "source-enrichment",
    "analysis",
    "topic-analysis",
    "notifications",
]
JOB_KINDS = get_args(JobKind)
MAX_EVENT_BYTES = 16384
logger = logging.getLogger(__name__)


class JobLogEntry(BaseModel):
    id: str
    timestamp: datetime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    message: str
    fields: dict[str, JsonValue]


class JobLogPage(BaseModel):
    items: list[JobLogEntry]
    next_cursor: str | None
    has_more: bool
    truncated: bool
    unreadable_entries: int
    retention_seconds: int
    max_entries: int


def validate_cursor(cursor: str) -> str:
    if not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,20}", cursor) or any(
        int(part) > 2**64 - 1 for part in cursor.split("-")
    ):
        raise ValueError("Invalid log cursor")
    return cursor


def log_keys(kind: JobKind, job_id: UUID) -> tuple[str, str]:
    if kind not in JOB_KINDS:
        raise ValueError("Unknown job kind")
    prefix = f"devfeed:job-logs:{{{kind}:{UUID(str(job_id))}}}"
    return prefix, prefix + ":count"


def read_job_logs(
    redis: Redis, kind: JobKind, job_id: UUID, *, after: str | None = None, limit: int = 100
) -> JobLogPage:
    settings = get_settings()
    if not 1 <= limit <= 500:
        raise ValueError("Invalid log page size")
    start = "(" + validate_cursor(after) if after is not None else "-"
    key, count_key = log_keys(kind, job_id)
    with redis.pipeline(transaction=True) as pipe:
        pipe.xrange(key, min=start, max="+", count=limit + 1)
        pipe.xlen(key)
        pipe.get(count_key)
        rows, retained, written = pipe.execute()
    items = []
    cursor, unreadable = after, 0
    for identifier, values in rows[:limit]:
        cursor = identifier.decode() if isinstance(identifier, bytes) else identifier
        try:
            raw = values.get(b"event", values.get("event", ""))
            if len(raw) > MAX_EVENT_BYTES:
                raise ValueError("Oversized log entry")
            items.append(JobLogEntry.model_validate({**json.loads(raw), "id": cursor}))
        except (ValueError, TypeError, ValidationError):
            # Advance past malformed entries, never return their raw contents.
            unreadable += 1
    return JobLogPage(
        items=items,
        next_cursor=cursor,
        has_more=len(rows) > limit,
        truncated=int(written or retained) > retained,
        unreadable_entries=unreadable,
        retention_seconds=settings.job_log_ttl_seconds,
        max_entries=settings.job_log_max_entries,
    )


class JobLogHandler(logging.Handler):
    """Best-effort synchronous writes with short deadlines and an outage cooldown.

    Created in the RQ parent; each fork lazily creates its own Redis client.
    Nothing is buffered in process, so completed records survive a worker crash.
    """

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.payload_formatter = JsonFormatter("worker")
        self.client: Redis | None = None
        self.pid: int | None = None
        self.retry_at = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        if time.monotonic() < self.retry_at:
            return
        try:
            payload = self.payload_formatter.payload(record)
            kind, identifier = payload.get("job_kind"), payload.get("job_id")
            if kind not in JOB_KINDS or not identifier:
                return
            key, count_key = log_keys(cast(JobKind, kind), UUID(str(identifier)))
            message = event_text(payload)
            error = error_text(payload, verbose=True)
            entry = {
                "timestamp": payload.pop("timestamp"),
                "level": payload.pop("level"),
                "message": message + (f" - {error}" if error else ""),
                "fields": payload,
            }
            encoded = json.dumps(entry, ensure_ascii=True, allow_nan=False)
            if len(encoded) > MAX_EVENT_BYTES:
                # Most space is exception frames; retain a bounded tail per cause.
                for cause in payload.get("exception", []):
                    cause["frames"] = cause["frames"][-5:]
                entry["message"] = entry["message"][:1000]
                payload["details_truncated"] = True
                encoded = json.dumps(entry, ensure_ascii=True, allow_nan=False)
                if len(encoded) > MAX_EVENT_BYTES:
                    entry["fields"] = {
                        "event": payload["event"],
                        "job_kind": kind,
                        "job_id": str(UUID(str(identifier))),
                        "details_truncated": True,
                    }
                    encoded = json.dumps(entry, ensure_ascii=True, allow_nan=False)
            settings = get_settings()
            if self.pid != os.getpid() or self.client is None:
                self.client = create_redis(
                    settings,
                    socket_connect_timeout=0.2,
                    socket_timeout=0.2,
                    retry=Retry(NoBackoff(), 0),
                )
                self.pid = os.getpid()
            with self.client.pipeline(transaction=True) as pipe:
                pipe.xadd(
                    key, {"event": encoded}, maxlen=settings.job_log_max_entries, approximate=False
                )
                pipe.incr(count_key)
                pipe.expire(key, settings.job_log_ttl_seconds)
                pipe.expire(count_key, settings.job_log_ttl_seconds)
                pipe.execute()
        except Exception:
            # Set the cooldown before warning: our own handler will skip this
            # record, while the existing console handler keeps its chosen format.
            # Never include raw errors (Redis errors can contain credentials).
            self.retry_at = time.monotonic() + 30
            with suppress(Exception):
                logger.warning("job_log_storage_unavailable")

    def close(self) -> None:
        if self.client is not None and self.pid == os.getpid():
            with suppress(Exception):
                self.client.close()
        super().close()


@contextmanager
def capture_runtime_logs() -> Iterator[None]:
    """Install only in workers; console level/format remain unchanged."""
    handler = JobLogHandler()
    root = logging.getLogger()
    namespaces = [
        logging.getLogger(name)
        for name in ("devfeed_core", "devfeed_aggregator", "devfeed_notifications")
    ]
    previous = [namespace.level for namespace in namespaces]
    root.addHandler(handler)
    for namespace in namespaces:
        namespace.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
        for namespace, level in zip(namespaces, previous, strict=True):
            namespace.setLevel(level)


@contextmanager
def job_log_context(kind: JobKind, job_id: str) -> Iterator[None]:
    with log_context(service="worker", job_kind=kind, job_id=job_id):
        started = time.perf_counter()
        logger.debug("job_execution_started")
        yield
        logger.debug("job_execution_finished", extra={"duration_ms": elapsed_ms(started)})
