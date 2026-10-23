"""Logging stays bounded and private through corrupt storage and stalled output.

Redis supplies read results and accepts recorded writes. These tests do not
emulate its stream selection, atomicity, eviction, or persistence guarantees.
"""

import io
import json
import logging
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from devfeed_core import job_logs
from devfeed_core import logging as application_logging


def record(**values):
    return logging.makeLogRecord(
        {"name": "devfeed_core.tests", "levelno": logging.INFO, "levelname": "INFO", **values}
    )


@pytest.fixture
def log_store(monkeypatch):
    client, pipeline = MagicMock(), MagicMock()
    client.pipeline.return_value.__enter__.return_value = pipeline
    monkeypatch.setattr(job_logs, "create_redis", lambda *_args, **_kw: client)
    return client, pipeline


def _chained_dependency_failure_with_context(depth, cause):
    if depth:
        return _chained_dependency_failure_with_context(depth - 1, cause)
    raise RuntimeError("password=private-token") from cause


def test_real_chained_tracebacks_keep_bounded_tails_and_redact_credentials(log_store):
    client, pipeline = log_store
    cause = None
    for _ in range(5):
        try:
            _chained_dependency_failure_with_context(35, cause)
        except RuntimeError as error:
            cause = error
    entry = record(
        msg="article_analysis_failed",
        job_kind="analysis",
        job_id=str(uuid.uuid4()),
        exc_info=(type(cause), cause, cause.__traceback__),
    )
    handler = job_logs.JobLogHandler()
    try:
        # Real formatter output exceeds storage's envelope before tail reduction.
        assert len(json.dumps(handler.payload_formatter.payload(entry))) > job_logs.MAX_EVENT_BYTES
        handler.emit(entry)
        encoded = pipeline.xadd.call_args.args[1]["event"]
        parsed = json.loads(encoded)
        assert len(encoded) <= job_logs.MAX_EVENT_BYTES and "private-token" not in encoded
        assert parsed["fields"]["details_truncated"] is True
        assert len(parsed["fields"]["exception"]) == 5
        assert all(len(cause["frames"]) <= 5 for cause in parsed["fields"]["exception"])
        assert "[REDACTED]" in encoded
    finally:
        handler.close()
    client.close.assert_called_once_with()


def test_large_normal_context_falls_back_to_a_small_actionable_job_receipt(log_store):
    _, pipeline = log_store
    identifier = uuid.uuid4()
    handler = job_logs.JobLogHandler()
    try:
        handler.emit(
            record(
                msg="article_analysis_started",
                job_kind="analysis",
                job_id=str(identifier),
                body="private-workload" * job_logs.MAX_EVENT_BYTES,
            )
        )
        encoded = pipeline.xadd.call_args.args[1]["event"]
        parsed = json.loads(encoded)
        assert len(encoded) <= job_logs.MAX_EVENT_BYTES and "private-workload" not in encoded
        assert parsed["fields"] == {
            "event": "article_analysis_started",
            "job_kind": "analysis",
            "job_id": str(identifier),
            "details_truncated": True,
        }
        assert parsed["message"]
    finally:
        handler.close()


def test_oversized_saved_event_advances_the_cursor_without_disclosing_or_looping(log_store):
    client, pipeline = log_store
    normal = {
        "timestamp": "2026-10-23T00:00:00+00:00",
        "level": "INFO",
        "message": "Article analysis started",
        "fields": {"event": "article_analysis_started"},
    }
    oversized = json.dumps({**normal, "message": "private-workload" * job_logs.MAX_EVENT_BYTES})
    valid = (b"2-0", {b"event": json.dumps(normal).encode()})
    pipeline.execute.side_effect = [
        ([(b"1-0", {b"event": oversized.encode()}), valid], 2, 2),
        ([valid], 2, 2),
    ]
    identifier = uuid.uuid4()

    first = job_logs.read_job_logs(client, "analysis", identifier, limit=1)
    second = job_logs.read_job_logs(
        client, "analysis", identifier, after=first.next_cursor, limit=1
    )

    assert first.items == [] and first.unreadable_entries == 1
    assert first.has_more and first.next_cursor == "1-0"
    assert "private-workload" not in first.model_dump_json()
    assert len(second.items) == 1 and second.items[0].id == "2-0"
    assert second.next_cursor == "2-0" and not second.has_more
    assert second.unreadable_entries == 0
    assert pipeline.xrange.call_args_list[1].kwargs["min"] == "(1-0"


@pytest.mark.parametrize("limit", [0, 501])
def test_unbounded_log_read_is_rejected_before_any_storage_work(log_store, limit):
    client, _ = log_store
    with pytest.raises(ValueError, match="Invalid log page size"):
        job_logs.read_job_logs(client, "analysis", uuid.uuid4(), limit=limit)
    client.pipeline.assert_not_called()


@pytest.fixture
def output(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(application_logging, "sys", SimpleNamespace(stderr=stream))
    return stream


def test_shutdown_refuses_late_records_instead_of_leaving_work_without_a_consumer(output):
    handler = application_logging.QueuedStderrHandler(capacity=1)
    handler.close()
    handler.emit(record(msg="late_shutdown_record"))
    assert handler.pending.empty() and handler.pending.unfinished_tasks == 0
    assert output.getvalue() == ""


def test_quiet_library_record_does_not_enter_the_output_queue(output):
    handler = application_logging.QueuedStderrHandler(capacity=1)
    handler.setFormatter(application_logging.TextFormatter("api"))
    try:
        handler.emit(record(name="httpx", msg="routine_dependency_message"))
        handler.flush()
        assert output.getvalue() == ""
    finally:
        handler.close()


def test_failed_queued_formatting_emits_a_safe_fallback_instead_of_raw_input(output):
    class BrokenFormatter(logging.Formatter):
        def format(self, record):
            raise ValueError("private formatter state")

    handler = application_logging.QueuedStderrHandler(capacity=1)
    handler.setFormatter(BrokenFormatter())
    try:
        handler.emit(record(msg="private unformatted input"))
        handler.flush()
        assert output.getvalue() == "logging_error: unable to format application log\n"
    finally:
        handler.close()


def test_explicit_flush_has_a_bounded_wait_when_the_collector_and_queue_are_full(monkeypatch):
    entered, release, flushed = threading.Event(), threading.Event(), threading.Event()

    class StalledOutput:
        def write(self, line):
            entered.set()
            assert release.wait(5)

        def flush(self):
            pass

    monkeypatch.setattr(application_logging, "sys", SimpleNamespace(stderr=StalledOutput()))
    handler = application_logging.QueuedStderrHandler(capacity=1)
    handler.setFormatter(application_logging.JsonFormatter("api"))

    def flush():
        handler.flush()
        flushed.set()

    thread = threading.Thread(target=flush)
    try:
        handler.emit(record(msg="first_record"))
        assert entered.wait(2)
        handler.emit(record(msg="queued_record"))
        assert handler.pending.full()
        thread.start()
        assert flushed.wait(3), "Explicit flush blocked behind a stalled collector"
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(timeout=2)
        handler.close()
    assert not handler.worker.is_alive() and not thread.is_alive()
