import asyncio
import json
import logging
import sys
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from devfeed_aggregator import scheduler, tasks, worker
from devfeed_cli import commands
from devfeed_cli.main import run
from devfeed_core.config import get_settings
from devfeed_core.feeds import validation
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.logging import (
    JsonFormatter,
    StderrHandler,
    TextFormatter,
    configure_logging,
    log_context,
)
from devfeed_core.models import Source, utcnow
from devfeed_core.urls import fingerprint

logger = logging.getLogger("devfeed_core.tests")


@pytest.fixture(autouse=True)
def logging_state():
    loggers = [logging.getLogger()] + [
        item
        for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    ]
    state = [
        (item, item.level, item.handlers[:], item.propagate, item.disabled) for item in loggers
    ]
    handlers = [
        (handler, handler.level, handler.formatter) for item in loggers for handler in item.handlers
    ]
    yield
    for item, level, saved_handlers, propagate, disabled in state:
        item.setLevel(level)
        item.handlers[:] = saved_handlers
        item.propagate, item.disabled = propagate, disabled
    for handler, level, formatter in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)


@pytest.fixture
def json_logs(monkeypatch, capsys):
    monkeypatch.setenv("DEVFEED_LOG_FORMAT", "json")
    monkeypatch.setenv("DEVFEED_LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()
    configure_logging("test", "DEBUG", "json")

    def read():
        output = capsys.readouterr()
        return output.out, [
            json.loads(line) for line in output.err.splitlines() if line.startswith("{")
        ]

    return read


@pytest.mark.parametrize("formatter", [JsonFormatter, TextFormatter])
def test_formats_keep_metadata_without_payloads_or_exception_messages(formatter):
    try:
        try:
            raise ValueError("redis://operator:private-password@cache/0")
        except ValueError as cause:
            raise RuntimeError("SELECT private-article-body; token=private-token") from cause
    except RuntimeError:
        record = logging.LogRecord(
            "devfeed_core.tests",
            logging.ERROR,
            __file__,
            10,
            "operation_failed",
            (),
            sys.exc_info(),
        )
    record.source_id = uuid.uuid4()
    record.body = "private-article-body"
    record.database_url = "postgresql://operator:private-password@database/app"
    record.authorization = "private-token"
    with log_context(request_id="request-123"):
        renderer = (
            formatter("api", verbose=True) if formatter is TextFormatter else formatter("api")
        )
        output = renderer.format(record)
    assert "operation_failed" in output and "request-123" in output
    assert str(record.source_id) in output
    assert "RuntimeError" in output and "ValueError" in output
    assert "test_formats_keep_metadata" in output
    assert "private-" not in output and "SELECT" not in output
    assert len(output.splitlines()) == 1


@pytest.mark.parametrize(
    "command,action",
    [("sources", "fetch"), ("jobs", "show"), ("jobs", "retry"), ("jobs", "dispatch")],
)
def test_cli_job_results_log_job_and_source_ids_separately(json_logs, monkeypatch, command, action):
    from devfeed_cli import main

    job_id, source_id = uuid.uuid4(), uuid.uuid4()

    def configure(parser):
        parser.set_defaults(
            command=command,
            action=action,
            execute=lambda _: {"id": str(job_id), "source_id": str(source_id), "status": "queued"},
        )

    monkeypatch.setattr(main, "configure", configure)
    assert run([]) == 0
    _, events = json_logs()
    result = next(event for event in events if event["event"] == "command_succeeded")
    assert result["job_id"] == str(job_id)
    assert result["source_id"] == str(source_id)


@pytest.mark.parametrize("formatter", [JsonFormatter, TextFormatter])
def test_library_logs_do_not_format_raw_messages_arguments_or_sql(formatter):
    record = logging.LogRecord(
        "rq.worker",
        logging.ERROR,
        __file__,
        10,
        "job failed: password=private-password %s",
        ("article-body-private",),
        None,
    )
    output = formatter("worker").format(record)
    assert "rq.worker" in output
    assert ("dependency_log" if formatter is JsonFormatter else "library message omitted") in output
    assert "private" not in output
    record.msg, record.args = "privatepassword", ()
    assert "privatepassword" not in formatter("worker").format(record)


@pytest.mark.parametrize("formatter", [JsonFormatter, TextFormatter])
def test_fields_are_bounded_escaped_and_defensively_redacted(formatter):
    record = logging.makeLogRecord({"name": "devfeed_core.tests", "msg": "safe_event"})
    record.worker_name = "worker\nforged-log"
    record.reason = "https://operator:private-password@example.com/feed?secret=token"
    record.duration_ms = float("nan")
    cyclic = []
    cyclic.append(cyclic)
    record.changed_fields = cyclic
    renderer = (
        formatter("worker", verbose=True) if formatter is TextFormatter else formatter("worker")
    )
    output = renderer.format(record)
    assert len(output.splitlines()) == 1 and "[redacted-url]" in output
    assert "private-password" not in output and "secret=token" not in output
    assert "[omitted]" in output


def test_formatter_failure_does_not_dump_raw_record(capsys):
    class BrokenFormatter(logging.Formatter):
        def format(self, record):
            raise RuntimeError("private-password")

    handler = StderrHandler()
    handler.setFormatter(BrokenFormatter())
    handler.handle(logging.makeLogRecord({"msg": "private-article-body"}))
    assert capsys.readouterr().err == "logging_error: unable to emit application log\n"


def test_configuration_is_idempotent_and_logs_only_to_stderr(capsys):
    configure_logging("cli", "INFO", "json")
    configure_logging("cli", "INFO", "json")
    logger.info("one_event")
    output = capsys.readouterr()
    assert output.out == ""
    assert len(output.err.splitlines()) == 1
    assert json.loads(output.err)["event"] == "one_event"
    assert sum(isinstance(item, StderrHandler) for item in logging.getLogger().handlers) == 1


def test_structured_fields_do_not_conflict_with_logging_record_attributes():
    from devfeed_core.logging import _FIELDS

    record = logging.makeLogRecord({})
    assert not _FIELDS.intersection(record.__dict__)


def test_plain_text_default_format_and_level_filter(capsys):
    configure_logging("cli", "WARNING")
    logger.info("hidden_event")
    logger.warning("visible_event", extra={"duration_ms": 12.5})
    output = capsys.readouterr()
    assert output.out == ""
    assert "[cli] Visible event" in output.err
    assert "12 ms" in output.err and "Hidden event" not in output.err
    assert not output.err.startswith("{")


def test_text_ingestion_summary_has_counts_and_short_id_without_context_dump():
    job_id = str(uuid.uuid4())
    record = logging.makeLogRecord(
        {
            "name": "devfeed_aggregator.tasks",
            "msg": "ingestion_succeeded",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "job_id": job_id,
            "source_id": str(uuid.uuid4()),
            "command_id": str(uuid.uuid4()),
            "worker_name": "a-verbose-worker-name",
            "entries_seen": 25,
            "articles_created": 17,
            "entries_skipped": 0,
            "duration_ms": 1200,
            "upstream_status": 200,
        }
    )
    output = TextFormatter("worker").format(record)
    assert f"Ingested 25 entries: 17 new, 0 skipped (job {job_id[:8]}; 1.20 s)" in output
    assert len(output.splitlines()) == 1
    assert job_id not in output
    for field in ["logger=", "pid=", "command_id=", "source_id=", "worker_name=", "null"]:
        assert field not in output


def test_admin_service_logs_keep_human_events_and_exclude_auth_secrets():
    record = logging.makeLogRecord(
        {
            "name": "devfeed_admin_api.auth",
            "msg": "admin_signed_in",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "cookie": "private-cookie",
            "code": "private-code",
            "nonce": "private-nonce",
        }
    )
    output = TextFormatter("admin-api").format(record)
    assert "[admin-api] Administrator signed in" in output
    assert "private-" not in output


def test_text_request_summary_uses_full_url_and_duration():
    identifier = str(uuid.uuid4())
    url = f"http://192.168.1.101:8001/v1/admin/sources/{identifier}"
    record = logging.makeLogRecord(
        {
            "name": "devfeed_api.logging",
            "msg": "request_completed",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "method": "GET",
            "route": f"/v1/admin/sources/{identifier}",
            "request_url": url,
            "status_code": 200,
            "duration_ms": 8,
            "request_id": str(uuid.uuid4()),
        }
    )
    output = TextFormatter("api").format(record)
    assert f"[api] GET {url} -> 200 (8 ms)" in output
    assert "request_id" not in output


def test_attribute_error_shows_safe_identifiers_without_object_repr():
    class PrivateObject:
        def __repr__(self):
            pytest.fail("Object representation must not be inspected")

    try:
        _ = PrivateObject().source_type
    except AttributeError:
        record = logging.LogRecord(
            "devfeed_aggregator.tasks",
            logging.ERROR,
            __file__,
            1,
            "ingestion_runtime_failed",
            (),
            sys.exc_info(),
        )
    output = TextFormatter("worker").format(record)
    assert "Ingestion crashed - AttributeError: PrivateObject.source_type is missing" in output
    assert "test_logging.py:" in output
    assert "exception=[" not in output and "frames" not in output
    detail = json.loads(JsonFormatter("worker").format(record))["exception"][0]
    assert detail["attribute"] == "source_type" and detail["object_type"] == "PrivateObject"


def test_plain_text_suppresses_duplicate_rq_failure_but_keeps_root_error(monkeypatch, capsys):
    configure_logging("worker", "INFO", "text")

    def fail(_):
        raise RuntimeError("private-object-data")

    monkeypatch.setattr(tasks, "_ingest", fail)
    job_id = str(uuid.uuid4())
    try:
        tasks.ingest(job_id)
    except RuntimeError:
        record = logging.LogRecord(
            "rq.worker",
            logging.ERROR,
            "base.py",
            1,
            "private-traceback",
            (),
            None,
            func="handle_exception",
        )
        logging.getLogger("rq.worker").handle(record)
        assert worker.log_job_exception(SimpleNamespace(args=[job_id], id="rq-id"), *sys.exc_info())
    output = capsys.readouterr()
    assert len(output.err.splitlines()) == 1
    assert "Ingestion crashed - RuntimeError" in output.err
    assert "private-" not in output.err


def test_plain_text_keeps_rq_failure_before_ingestion_starts(capsys):
    configure_logging("worker", "INFO", "text")
    try:
        raise ImportError("private-path")
    except ImportError:
        worker.log_job_exception(
            SimpleNamespace(args=[str(uuid.uuid4())], id="rq-id"), *sys.exc_info()
        )
    output = capsys.readouterr()
    assert "Worker job failed - ImportError" in output.err
    assert "private-path" not in output.err


def test_plain_text_quiets_library_info_without_hiding_warnings(capsys):
    configure_logging("worker", "INFO", "text")
    library = logging.getLogger("rq.worker")
    library.info("private startup details")
    assert capsys.readouterr().err == ""
    library.warning("private warning details")
    output = capsys.readouterr().err
    assert "rq.worker: warning" in output and "private" not in output


def test_text_service_controls_and_nonstring_messages_are_safe(capsys):
    configure_logging("cli", "INFO", "text")
    with log_context(service="cli\nFORGED"):
        logger.error("safe_event")
    output = capsys.readouterr().err
    assert len(output.splitlines()) == 1 and "\\nFORGED" in output
    logger.error({"password": "private-payload"})
    output = capsys.readouterr().err
    assert len(output.splitlines()) == 1 and "private-payload" not in output


def test_library_handlers_share_output_and_access_logs_are_disabled(json_logs):
    configure_logging("worker", "DEBUG", "json")
    from rq.logutils import setup_loghandlers

    setup_loghandlers("INFO")
    assert logging.getLogger("rq.worker").handlers == []
    assert logging.getLogger("uvicorn.error").propagate is True
    logging.getLogger("uvicorn.access").info("private URL")
    logging.getLogger("httpcore").debug("private HTTP headers")
    logging.getLogger("sqlalchemy.engine").info("private SQL parameters")
    assert json_logs() == ("", [])


def test_context_is_nested_and_restored_even_on_failure(json_logs):
    with log_context(request_id="outer"):
        with pytest.raises(RuntimeError), log_context(request_id="inner", job_id="job"):
            logger.info("inner_event")
            raise RuntimeError()
        logger.info("outer_event")
    logger.info("no_context")
    _, events = json_logs()
    assert events[0]["request_id"] == "inner" and events[0]["job_id"] == "job"
    assert events[1]["request_id"] == "outer" and "job_id" not in events[1]
    assert "request_id" not in events[2]


def test_concurrent_request_contexts_do_not_leak(json_logs):
    from devfeed_api.logging import RequestLoggingMiddleware

    async def app(scope, receive, send):
        await asyncio.sleep(0)
        logger.info("inside_request")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def scenario():
        async def request():
            messages = []

            async def send(message):
                messages.append(message)

            async def receive():
                return {"type": "http.request", "body": b""}

            await RequestLoggingMiddleware(app)(
                {"type": "http", "method": "GET", "path": "/requests/" + str(uuid.uuid4())},
                receive,
                send,
            )
            return dict(messages[0]["headers"])[b"x-request-id"].decode()

        return await asyncio.gather(request(), request())

    request_ids = asyncio.run(scenario())
    _, events = json_logs()
    assert len(set(request_ids)) == 2
    for request_id in request_ids:
        assert [item["event"] for item in events if item.get("request_id") == request_id] == [
            "inside_request",
            "request_completed",
        ]
        assert (
            len({item["request_url"] for item in events if item.get("request_id") == request_id})
            == 1
        )
    assert (
        len({item["request_url"] for item in events if item.get("request_id") in request_ids}) == 2
    )


def test_api_logs_routes_status_and_request_id_in_threadpool(json_logs):
    from devfeed_api.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()

    @app.get("/probe/{item_id}")
    def probe(item_id: str):
        logger.info("probe_called")
        return {"ok": True}

    with TestClient(app) as client:
        json_logs()
        response = client.get(
            "/probe/record-123?q=private-query&limit=25",
            headers={"Authorization": "private-header", "X-Request-ID": "untrusted"},
        )
        _, events = json_logs()
    request_id = response.headers["X-Request-ID"]
    assert request_id != "untrusted" and uuid.UUID(request_id)
    assert all(item["request_id"] == request_id for item in events)
    assert events[0]["event"] == "probe_called"
    assert events[-1]["event"] == "request_completed"
    assert all(item["route"] == "/probe/record-123" for item in events)
    assert all(
        item["request_url"] == "http://testserver/probe/record-123?q=[redacted]&limit=25"
        for item in events
    )
    assert events[-1]["status_code"] == 200 and events[-1]["duration_ms"] >= 0
    assert "private-" not in json.dumps(events)


def test_api_500_has_same_request_id_and_safe_stack(json_logs):
    from devfeed_api.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()

    @app.get("/broken")
    def broken():
        raise RuntimeError("private-password")

    with TestClient(app, raise_server_exceptions=False) as client:
        json_logs()
        response = client.get("/broken")
        _, events = json_logs()
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    failure = next(item for item in events if item["event"] == "request_failed")
    assert failure["error_type"] == "RuntimeError" and failure["exception"]
    assert failure["request_id"] == response.headers["X-Request-ID"]
    assert events[-1]["status_code"] == 500
    assert "private-password" not in json.dumps(events)


def test_health_checks_are_quiet_but_errors_are_visible(json_logs):
    from devfeed_api.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        configure_logging("api", "INFO", "json")
        json_logs()
        assert client.get("/health/live").status_code == 200
        assert json_logs() == ("", [])
        assert client.get("/unmatched-path").status_code == 404
        _, events = json_logs()
    assert events[0]["level"] == "WARNING" and events[0]["route"] == "/unmatched-path"
    assert events[0]["request_url"] == "http://testserver/unmatched-path"


def test_cli_keeps_json_stdout_separate_and_correlates_logs(json_logs, monkeypatch):
    monkeypatch.setattr(commands.scheduler, "tick", lambda: {"dispatched": 3})
    assert run(["scheduler", "--once"]) == 0
    output, events = json_logs()
    assert json.loads(output) == {"dispatched": 3}
    assert [item["event"] for item in events] == [
        "command_started",
        "command_succeeded",
        "command_completed",
    ]
    assert len({item["command_id"] for item in events}) == 1
    assert events[-1]["exit_code"] == 0


def test_cli_unexpected_failure_has_safe_diagnostics(json_logs, monkeypatch):
    def fail():
        raise RuntimeError("private-password")

    monkeypatch.setattr(commands.scheduler, "tick", fail)
    assert run(["scheduler", "--once"]) == 1
    output, events = json_logs()
    assert output == ""
    assert "private-password" not in json.dumps(events)
    assert any(item["event"] == "command_failed" and item["exception"] for item in events)
    assert events[-1]["exit_code"] == 1


def test_feed_validation_has_fingerprint_timing_and_safe_failure_reason(json_logs, monkeypatch):
    url = "https://example.com/rss?key=private-query-token"
    monkeypatch.setattr(validation, "fetch_feed", lambda url: FetchResult(200, b"<html/>", url))
    with pytest.raises(validation.FeedValidationError):
        validation.validate_feed(url, source_type="publisher")
    _, events = json_logs()
    assert events[0]["event"] == "feed_validation_started"
    assert events[-1]["event"] == "feed_validation_failed"
    assert events[-1]["reason"] == "unreadable_feed"
    assert events[-1]["feed_id"] == fingerprint(url)
    assert events[-1]["duration_ms"] >= 0
    assert "private-query-token" not in json.dumps(events)


@pytest.mark.parametrize("active", [False, True])
def test_scheduler_tick_metrics_and_idle_log_level(json_logs, monkeypatch, active):
    result = {
        "scheduled": int(active),
        "dispatched": int(active),
        "recovered": 0,
        "images_dispatched": 0,
        "images_recovered": 0,
    }
    monkeypatch.setattr(scheduler, "_tick", lambda: result)
    assert scheduler.tick() == result
    _, events = json_logs()
    assert events[-1]["event"] == "scheduler_tick_completed"
    assert events[-1]["level"] == ("INFO" if active else "DEBUG")
    assert events[-1]["service"] == "scheduler" and events[-1]["tick_id"]


def test_scheduler_failure_has_safe_exception(json_logs, monkeypatch):
    def fail():
        raise RuntimeError("private-redis-url")

    monkeypatch.setattr(scheduler, "_tick", fail)
    with pytest.raises(RuntimeError):
        scheduler.tick()
    _, events = json_logs()
    assert events[-1]["event"] == "scheduler_tick_failed"
    assert events[-1]["error_type"] == "RuntimeError"
    assert "private-redis-url" not in json.dumps(events)


@pytest.fixture
def fake_ingestion(monkeypatch):
    job = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        status="running",
        lease_token=uuid.uuid4(),
        attempts=1,
        available_at=utcnow(),
        entries_seen=0,
        entries_skipped=0,
        articles_created=0,
    )
    source = SimpleNamespace(
        id=job.source_id,
        feed_url="https://example.com/rss",
        source_type="publisher",
        approval_status="approved",
        etag=None,
        last_modified=None,
        enabled=True,
        poll_interval_seconds=1800,
    )
    commits = []

    @contextmanager
    def begin():
        yield SimpleNamespace(
            scalar=lambda stmt: source if stmt.column_descriptions[0]["entity"] is Source else job,
            get=lambda *args: source,
        )
        commits.append(True)
        logger.debug("test_transaction_committed")

    monkeypatch.setattr(tasks, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(tasks, "claim_job", lambda *args: (job, source))
    return job, source, commits


def test_ingestion_success_is_logged_after_commit(json_logs, fake_ingestion, monkeypatch):
    job, source, commits = fake_ingestion
    monkeypatch.setattr(tasks, "fetch_feed", lambda *args: FetchResult(304, b"", source.feed_url))
    tasks.ingest(str(job.id))
    _, events = json_logs()
    assert events[-1]["event"] == "job_execution_finished"
    events = events[:-1]
    success = events[-1]
    assert success["event"] == "ingestion_succeeded" and success["upstream_status"] == 304
    assert success["job_id"] == str(job.id) and success["source_id"] == str(source.id)
    assert success["attempt"] == 1 and success["duration_ms"] >= 0
    assert events[-2]["event"] == "test_transaction_committed" and len(commits) == 2


@pytest.mark.parametrize("retryable", [False, True])
def test_ingestion_failures_log_retry_or_terminal_outcome(
    json_logs, fake_ingestion, monkeypatch, retryable
):
    job, source, commits = fake_ingestion

    def fail(*args):
        raise FeedError("private-body", retryable=retryable, status=503, reason="http_error")

    def record_failure(session, job, error, **kwargs):
        job.status = "queued" if kwargs["retryable"] else "failed"

    monkeypatch.setattr(tasks, "fetch_feed", fail)
    monkeypatch.setattr(tasks, "fail_job", record_failure)
    tasks.ingest(str(job.id))
    _, events = json_logs()
    assert events[-1]["event"] == "job_execution_finished"
    events = events[:-1]
    assert events[-1]["event"] == ("ingestion_retry_scheduled" if retryable else "ingestion_failed")
    assert events[-2]["event"] == "test_transaction_committed"
    assert "private-body" not in json.dumps(events)


def test_ingestion_claim_failure_and_worker_error_have_job_context(json_logs, monkeypatch):
    job_id = str(uuid.uuid4())

    def fail():
        raise RuntimeError("private-database-password")

    monkeypatch.setattr(tasks, "session_factory", fail)
    with pytest.raises(RuntimeError):
        tasks.ingest(job_id)
    _, events = json_logs()
    assert events[-1]["event"] == "ingestion_runtime_failed"
    assert events[-1]["job_id"] == job_id
    assert "private-database-password" not in json.dumps(events)


def test_worker_configures_shared_logs_without_starting_worker(json_logs, monkeypatch):
    calls = []
    connection = SimpleNamespace(close=lambda: calls.append("close"))
    monkeypatch.setattr(worker, "get_queue", lambda: SimpleNamespace(connection=connection))

    def fake_worker(*args, **kwargs):
        assert kwargs["exception_handlers"] == [worker.log_job_exception]
        assert kwargs["work_horse_killed_handler"] == worker.log_work_horse_killed
        return SimpleNamespace(name="test-worker", work=lambda **kw: calls.append(kw))

    monkeypatch.setattr(worker, "Worker", fake_worker)
    worker.run(burst=True, name="test-worker")
    _, events = json_logs()
    assert [item["event"] for item in events] == ["worker_started", "worker_stopped"]
    assert all(item["worker_name"] == "test-worker" for item in events)
    assert calls[-1] == "close" and calls[0]["burst"] is True


def test_worker_initialization_failure_is_logged(json_logs, monkeypatch):
    def fail():
        raise RuntimeError("private-redis-password")

    monkeypatch.setattr(worker, "get_queue", fail)
    with pytest.raises(RuntimeError):
        worker.run(name="failing-worker")
    _, events = json_logs()
    assert events[-1]["event"] == "worker_runtime_failed"
    assert events[-1]["worker_name"] == "failing-worker"
    assert "private-redis-password" not in json.dumps(events)


def test_rq_exception_handler_keeps_failure_handling_and_logs_ids(json_logs):
    job_id = str(uuid.uuid4())
    try:
        raise RuntimeError("private-article")
    except RuntimeError:
        assert worker.log_job_exception(SimpleNamespace(args=[job_id], id="rq-id"), *sys.exc_info())
    _, events = json_logs()
    assert events[-1]["event"] == "rq_job_failed" and events[-1]["job_id"] == job_id
    assert events[-1]["error_type"] == "RuntimeError"
    assert "private-article" not in json.dumps(events)


def test_scheduler_dispatch_event_follows_commit(json_logs):
    job = SimpleNamespace(id=uuid.uuid4(), source_id=uuid.uuid4(), dispatched_at=None)

    @contextmanager
    def begin():
        yield SimpleNamespace(scalar=lambda *args: job)
        logger.debug("test_transaction_committed")

    queue = SimpleNamespace(enqueue=lambda *args, **kwargs: SimpleNamespace(id="rq-id"))
    assert scheduler.dispatch_jobs(SimpleNamespace(begin=begin), queue, 1, utcnow()) == 1
    _, events = json_logs()
    assert [item["event"] for item in events] == [
        "test_transaction_committed",
        "ingestion_dispatched",
    ]
    assert events[-1]["job_id"] == str(job.id) and events[-1]["rq_job_id"] == "rq-id"
