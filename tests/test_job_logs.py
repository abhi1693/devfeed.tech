"""Job logs are bounded, sanitized and independent of database commits."""

import json
import logging
import sys
import uuid
from types import SimpleNamespace

import pytest
from devfeed_aggregator import (
    analysis_tasks,
    article_tasks,
    image_tasks,
    source_tasks,
    tasks,
    worker,
)
from devfeed_core import job_logs
from devfeed_core.logging import configure_logging, log_context
from redis.exceptions import ConnectionError
from test_logging import logging_state as logging_state

logger = logging.getLogger("devfeed_core.tests")


class StreamStore:
    def __init__(self):
        self.streams, self.counts, self.ttls = {}, {}, {}
        self.sequence = 0
        self.closed = False
        self.unavailable = False
        self.executions = 0

    def pipeline(self, transaction=True):
        assert transaction
        return StreamPipeline(self)

    def close(self):
        self.closed = True


class StreamPipeline:
    def __init__(self, store):
        self.store, self.commands = store, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __getattr__(self, command):
        def enqueue(*args, **kwargs):
            self.commands.append((command, args, kwargs))
            return self

        return enqueue

    def execute(self):
        store = self.store
        store.executions += 1
        if store.unavailable:
            raise ConnectionError("redis://user:secret@redis.invalid")
        results = []
        for name, args, options in self.commands:
            key = args[0]
            if name == "xadd":
                assert options["approximate"] is False
                store.sequence += 1
                stream = store.streams.setdefault(key, [])
                stream.append(
                    (f"{store.sequence}-0".encode(), {b"event": args[1]["event"].encode()})
                )
                store.streams[key] = stream[-options["maxlen"] :]
                result = stream[-1][0]
            elif name == "incr":
                store.counts[key] = store.counts.get(key, 0) + 1
                result = store.counts[key]
            elif name == "expire":
                store.ttls[key] = args[1]
                result = True
            elif name == "get":
                result = store.counts.get(key)
            elif name == "xlen":
                result = len(store.streams.get(key, []))
            elif name == "xrange":
                rows = store.streams.get(key, [])
                if options["min"] != "-":
                    cursor = tuple(map(int, options["min"][1:].split("-")))
                    rows = [r for r in rows if tuple(map(int, r[0].decode().split("-"))) > cursor]
                result = rows[: options["count"]]
            else:
                pytest.fail(f"Unexpected Redis command: {name}")
            results.append(result)
        return results


@pytest.fixture
def stream_store(monkeypatch, logging_state):
    store = StreamStore()
    monkeypatch.setattr(job_logs.Redis, "from_url", lambda *a, **kw: store)
    configure_logging("worker", "INFO", "text")
    return store


def test_capture_does_not_change_console_level_or_leak_secrets(stream_store, capsys):
    identifier = uuid.uuid4()
    with job_logs.capture_runtime_logs():
        logger.info("outside_job")
        with job_logs.job_log_context("ingestion", str(identifier)), log_context(attempt=2):
            logger.debug("feed_fetch_started", extra={"body": "secret content"})
            try:
                raise ValueError("password=private-token")
            except ValueError:
                logger.exception("ingestion_runtime_failed", extra={"url": "https://secret"})
            logging.getLogger("httpx").warning("Bearer secret-token %s", "secret-body")
    page = job_logs.read_job_logs(stream_store, "ingestion", identifier)
    assert len(page.items) == 5
    assert page.items[1].level == "DEBUG" and page.items[1].fields["attempt"] == 2
    error = page.items[2]
    assert error.fields["error_type"] == "ValueError"
    assert error.fields["exception"][0]["frames"][-1]["file"] == "test_job_logs.py"
    assert page.items[3].fields["event"] == "dependency_log"
    for sensitive in ("secret content", "secret-token", "secret-body", "https://secret"):
        assert sensitive not in page.model_dump_json()
    assert "private-token" not in page.model_dump_json()
    assert "outside_job" not in page.model_dump_json()
    assert "feed_fetch_started" not in capsys.readouterr().err
    assert logging.getLogger("devfeed_core").level == logging.INFO
    assert not any(isinstance(h, job_logs.JobLogHandler) for h in logging.getLogger().handlers)
    assert stream_store.closed


@pytest.mark.parametrize(
    "module,entrypoint,internal,kind",
    [
        (tasks, "ingest", "_ingest", "ingestion"),
        (article_tasks, "enrich_article", "_enrich", "article-enrichment"),
        (image_tasks, "enrich_image", "_enrich", "images"),
        (source_tasks, "enrich_source", "_enrich_source", "source-enrichment"),
        (analysis_tasks, "analyze_article", "_analyze", "analysis"),
    ],
)
def test_every_job_captures_crashes_even_when_database_unavailable(
    stream_store, monkeypatch, module, entrypoint, internal, kind
):
    identifier = uuid.uuid4()

    def crash(*args):
        raise RuntimeError("private database connection")

    monkeypatch.setattr(module, internal, crash)
    with job_logs.capture_runtime_logs(), pytest.raises(RuntimeError):
        getattr(module, entrypoint)(str(identifier))
    page = job_logs.read_job_logs(stream_store, kind, identifier)
    assert len(page.items) == 2
    assert page.items[-1].level == "ERROR"
    assert page.items[-1].fields["job_kind"] == kind
    assert page.items[-1].fields["job_id"] == str(identifier)
    assert "private database" not in page.model_dump_json()


def test_limits_ttl_cursor_and_retry_isolation(stream_store, monkeypatch):
    monkeypatch.setenv("DEVFEED_JOB_LOG_MAX_ENTRIES", "100")
    identifier, unrelated = uuid.uuid4(), uuid.uuid4()
    with job_logs.capture_runtime_logs():
        for attempt in (1, 2):
            with log_context(job_kind="images", job_id=identifier, attempt=attempt):
                for _ in range(60):
                    logger.info("image_lookup_started")
        with log_context(job_kind="images", job_id=unrelated, attempt=1):
            logger.info("image_lookup_started")
    page = job_logs.read_job_logs(stream_store, "images", identifier, limit=60)
    assert len(page.items) == 60 and page.truncated and page.has_more
    assert [e.fields["attempt"] for e in page.items] == [1] * 40 + [2] * 20
    next_page = job_logs.read_job_logs(stream_store, "images", identifier, after=page.next_cursor)
    assert len(next_page.items) == 40 and not next_page.has_more
    assert all(e.fields["attempt"] == 2 for e in next_page.items)
    assert not ({e.id for e in page.items} & {e.id for e in next_page.items})
    assert all(ttl == 604800 for ttl in stream_store.ttls.values())
    empty = job_logs.read_job_logs(stream_store, "analysis", identifier)
    assert not empty.items and empty.next_cursor is None and not empty.truncated
    assert len(job_logs.read_job_logs(stream_store, "images", unrelated).items) == 1


@pytest.mark.parametrize(
    "cursor", ["", "1", "1-2-3", "-1-0", "+", "(1-0", "18446744073709551616-0", "x-0"]
)
def test_invalid_cursors_rejected_before_redis(cursor, stream_store):
    with pytest.raises(ValueError):
        job_logs.read_job_logs(stream_store, "analysis", uuid.uuid4(), after=cursor)
    assert stream_store.executions == 0


def test_corrupt_entries_are_skipped_without_cursor_loop_or_raw_data(stream_store):
    identifier = uuid.uuid4()
    key, _ = job_logs.log_keys("analysis", identifier)
    stream_store.streams[key] = [(b"1-0", {b"event": b"secret invalid json"})]
    page = job_logs.read_job_logs(stream_store, "analysis", identifier)
    assert not page.items and page.unreadable_entries == 1 and page.next_cursor == "1-0"
    assert "secret" not in page.model_dump_json()


def test_storage_failure_is_nonfatal_rate_limited_and_recovers(stream_store, capsys, monkeypatch):
    tick = [100.0]
    monkeypatch.setattr(job_logs.time, "monotonic", lambda: tick[0])
    stream_store.unavailable = True
    with job_logs.capture_runtime_logs(), log_context(job_kind="analysis", job_id=uuid.uuid4()):
        for _ in range(10):
            logger.info("article_analysis_started")
        assert stream_store.executions == 1
        stream_store.unavailable = False
        tick[0] += 31
        logger.info("article_analysis_completed")
        assert stream_store.executions == 2
    output = capsys.readouterr().err
    assert output.count("Job log storage unavailable") == 1
    assert "secret" not in output and "redis.invalid" not in output


def test_handler_lazily_creates_new_connection_after_fork(stream_store, monkeypatch):
    clients = []

    def connect(*args, **options):
        assert options["socket_timeout"] == 0.2
        clients.append(StreamStore())
        return clients[-1]

    monkeypatch.setattr(job_logs.Redis, "from_url", connect)
    with job_logs.capture_runtime_logs(), log_context(job_kind="images", job_id=uuid.uuid4()):
        assert not clients
        logger.info("image_lookup_started")
        monkeypatch.setattr(job_logs.os, "getpid", lambda: 99999999)
        logger.info("image_lookup_completed")
    assert len(clients) == 2 and clients[-1].closed


@pytest.mark.parametrize("function,kind", worker.JOB_FUNCTIONS.items())
def test_rq_failure_and_killed_process_callbacks_preserve_durable_job_id(
    stream_store, function, kind
):
    identifier = uuid.uuid4()
    job = SimpleNamespace(args=[str(identifier)], id="rq-delivery-id", func_name=function)
    with job_logs.capture_runtime_logs():
        try:
            raise RuntimeError("raw RQ exception secret")
        except RuntimeError:
            assert worker.log_job_exception(job, *sys.exc_info()) is True
        worker.log_work_horse_killed(job, 123, 9, None)
    page = job_logs.read_job_logs(stream_store, kind, identifier)
    assert [e.fields["event"] for e in page.items] == ["rq_job_failed", "rq_work_horse_killed"]
    assert all(e.fields["rq_job_id"] == "rq-delivery-id" for e in page.items)
    assert page.items[-1].fields["exit_code"] == 9
    assert "secret" not in page.model_dump_json()


def test_large_exception_payload_is_bounded(stream_store, monkeypatch):
    identifier = uuid.uuid4()
    original = job_logs.JsonFormatter.payload

    def oversized(self, record):
        result = original(self, record)
        result["exception"] = [
            {
                "type": "ValueError",
                "frames": [{"file": "f" * 250, "line": 1, "function": "run"}] * 100,
            }
        ]
        return result

    monkeypatch.setattr(job_logs.JsonFormatter, "payload", oversized)
    with job_logs.capture_runtime_logs(), log_context(job_kind="images", job_id=identifier):
        logger.info("image_lookup_started")
    key, _ = job_logs.log_keys("images", identifier)
    raw = stream_store.streams[key][0][1][b"event"]
    assert len(raw) <= job_logs.MAX_EVENT_BYTES
    assert json.loads(raw)["fields"]["details_truncated"] is True


@pytest.mark.integration
@pytest.mark.parametrize("kind", job_logs.JOB_KINDS)
def test_real_redis_stream_retention_and_exclusive_cursor(
    integration_environment, logging_state, monkeypatch, kind
):
    # Random test-owned keys only; no truncation/flush is needed for log storage.
    monkeypatch.setenv("DEVFEED_JOB_LOG_MAX_ENTRIES", "100")
    job_logs.get_settings.cache_clear()
    redis = job_logs.Redis.from_url(job_logs.get_settings().redis_url)
    identifier = uuid.uuid4()
    keys = job_logs.log_keys(kind, identifier)
    assert not redis.exists(*keys)
    try:
        with job_logs.capture_runtime_logs(), log_context(job_kind=kind, job_id=identifier):
            for _ in range(110):
                logger.debug("job_execution_started")
        first = job_logs.read_job_logs(redis, kind, identifier, limit=60)
        assert first.truncated and first.has_more and len(first.items) == 60
        second = job_logs.read_job_logs(redis, kind, identifier, after=first.next_cursor)
        assert len(second.items) == 40 and not second.has_more
        assert not ({entry.id for entry in first.items} & {entry.id for entry in second.items})
        assert all(604700 < redis.ttl(key) <= 604800 for key in keys)
    finally:
        redis.delete(*keys)
        redis.close()
        job_logs.get_settings.cache_clear()
