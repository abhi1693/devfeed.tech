"""Dependency observers fail safely and preserve private transport and process ownership.

SQLite exercises actual SQLAlchemy event lifecycles. DB-API setup and Redis are
command boundaries; these units do not prove PostgreSQL timeouts or Lua atomicity.
"""

import json
import logging
import socket
import sys
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import ai_capacity, database_telemetry, db, pipeline_capacity, telemetry
from devfeed_core.logging import JsonFormatter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, event, text

from tests.support.source_readiness import NOW, configuration


@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_database_setup_restores_connection_mode_even_when_the_session_setting_fails(
    autocommit, failure
):
    connection, cursor = MagicMock(), Mock()
    connection.autocommit = autocommit
    connection.cursor.return_value.__enter__.return_value = cursor

    def execute(statement):
        assert connection.autocommit is True
        assert statement == "SET statement_timeout = '30s'"
        if failure:
            raise OSError("private database credential")

    cursor.execute.side_effect = execute
    if failure:
        with pytest.raises(OSError):
            db.configure_connection(connection, None)
    else:
        db.configure_connection(connection, None)
    assert connection.autocommit is autocommit
    connection.cursor.return_value.__exit__.assert_called_once()


def test_health_revision_read_applies_a_local_short_budget_before_reading_the_revision():
    session = Mock()
    session.scalar.return_value = "0001"
    assert db.database_revision(session) == "0001"
    assert [str(call.args[0]) for call in session.mock_calls] == [
        "SET LOCAL statement_timeout = '2s'",
        "SELECT version_num FROM alembic_version",
    ]
    session.commit.assert_not_called()


def test_database_engine_enables_observation_without_opening_an_external_connection(monkeypatch):
    instrument = Mock()
    monkeypatch.setattr(database_telemetry, "instrument_engine", instrument)
    engine = db.create_database_engine(configuration(metrics_enabled=True))
    try:
        instrument.assert_called_once_with(engine)
        assert isinstance(engine.pool, database_telemetry.ObservedNullPool)
        assert engine.pool.status() == "NullPool"
    finally:
        engine.dispose()


def test_database_observer_can_run_without_telemetry_or_finish_after_runtime_is_removed(
    monkeypatch,
):
    runtime = telemetry.Runtime("unit-observer", configuration())
    runtime.provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    runtime.provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_runtime", None)
    engine = create_engine("sqlite://")
    database_telemetry.instrument_engine(engine)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        assert exporter.get_finished_spans() == ()
        monkeypatch.setattr(telemetry, "_runtime", runtime)

        def stop_observing(*_args):
            monkeypatch.setattr(telemetry, "_runtime", None)

        event.listen(engine, "before_cursor_execute", stop_observing)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT :private_value"), {"private_value": "private-record"}
                )
                == "private-record"
            )
        spans = exporter.get_finished_spans()
        assert len(spans) == 1 and spans[0].name == "postgresql select"
        assert dict(spans[0].attributes) == {
            "db.system.name": "postgresql",
            "db.operation.name": "select",
        }
    finally:
        engine.dispose()
        runtime.close()


def test_slow_database_warning_is_rate_limited_and_excludes_statement_and_parameters(
    monkeypatch, caplog
):
    clock = [100.0]
    monkeypatch.setattr(database_telemetry, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    runtime = telemetry.Runtime("unit-observer", configuration())
    monkeypatch.setattr(telemetry, "_runtime", runtime)
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def register_delay(connection, _record):
        def operation():
            clock[0] += 1.25
            return "private-row"

        connection.create_function("private_operation", 0, operation)

    database_telemetry.instrument_engine(engine)
    try:
        with (
            caplog.at_level(logging.WARNING, logger=database_telemetry.__name__),
            engine.connect() as connection,
        ):
            for index in range(3):
                if index == 2:
                    clock[0] += 30
                assert connection.scalar(text("SELECT private_operation()")) == "private-row"
        records = [
            record for record in caplog.records if record.name == database_telemetry.__name__
        ]
        assert len(records) == 2
        values = [JsonFormatter("unit-observer").payload(record) for record in records]
        assert all(
            value["event"] == "database_slow_operation" and value["duration_ms"] == 1250
            for value in values
        )
        assert "private" not in json.dumps(values) and "SELECT" not in json.dumps(values)
    finally:
        engine.dispose()
        runtime.close()


def test_failed_exporter_shutdown_is_logged_without_disclosing_its_exception(caplog):
    def failed():
        raise RuntimeError("private-exporter-token")

    with caplog.at_level(logging.WARNING, logger=telemetry.__name__):
        telemetry.bounded_shutdown(failed)
    assert [record.getMessage() for record in caplog.records] == ["telemetry_shutdown_failed"]
    assert "private" not in caplog.text


@pytest.mark.parametrize("profiling_failure", [False, True])
def test_occupied_metrics_port_and_optional_profiler_failure_do_not_abort_runtime_start(
    monkeypatch, caplog, profiling_failure
):
    profiler = SimpleNamespace(configure=Mock(), shutdown=Mock())
    if profiling_failure:
        profiler.configure.side_effect = RuntimeError("private-profiler-token")
    monkeypatch.setitem(sys.modules, "pyroscope", profiler)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        settings = configuration(
            metrics_enabled=True,
            metrics_host="127.0.0.1",
            metrics_port=occupied.getsockname()[1],
            pyroscope_server="http://profile.invalid",
        )
        runtime = telemetry.Runtime("worker-ingestion", settings)
        try:
            with caplog.at_level(logging.WARNING, logger=telemetry.__name__):
                runtime.start(serve_metrics=True, profiling=True, tracing=False)
            assert runtime.meter_provider is not None and runtime.metric_server.server is None
            values = profiler.configure.call_args.kwargs
            assert values["application_name"] == "devfeed-worker-ingestion"
            assert (
                values["report_pid"]
                is values["report_thread_id"]
                is values["report_thread_name"]
                is False
            )
            assert "private" not in caplog.text
            assert "metrics_listener_initialization_failed" in caplog.text
            if profiling_failure:
                assert runtime.profiler is None and "profiling_initialization_failed" in caplog.text
            else:
                assert runtime.profiler is profiler
        finally:
            runtime.close()
    assert profiler.shutdown.call_count == int(not profiling_failure)


def test_runtime_start_reuses_owned_instruments_but_an_inherited_runtime_cannot_shut_them_down(
    monkeypatch,
):
    settings = configuration(metrics_enabled=True)
    monkeypatch.setattr(telemetry, "get_settings", lambda: settings)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(telemetry, "_runtime", None)
    runtime = telemetry.start_runtime(
        "unit-observer", serve_metrics=False, profiling=False, tracing=False
    )
    assert telemetry.start_runtime("unit-observer") is runtime
    close = Mock(wraps=runtime.metric_server.close)
    monkeypatch.setattr(runtime.metric_server, "close", close)
    try:
        with monkeypatch.context() as fork:
            fork.setattr(telemetry, "os", SimpleNamespace(getpid=lambda: runtime.pid + 1))
            runtime.close()
            close.assert_not_called()
        telemetry.stop_runtime(runtime)
        close.assert_called_once()
        assert telemetry.current() is None
    finally:
        if telemetry.current() is runtime:
            telemetry.stop_runtime(runtime)


def test_no_observability_configuration_does_not_create_a_runtime(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(
        telemetry,
        "get_settings",
        lambda: configuration(metrics_enabled=False, otlp_endpoint=None, pyroscope_server=None),
    )
    monkeypatch.setattr(telemetry, "_runtime", None)
    assert telemetry.start_runtime("api") is None


@pytest.mark.parametrize(
    "reason,seconds,expected",
    [
        ("codex_usage_limit", 0, 300),
        ("codex_server_overloaded", 0, 30),
        ("codex_rate_limited", 100000, 86400),
    ],
)
@pytest.mark.parametrize("redis_failure", [False, True])
def test_provider_cooldown_is_bounded_even_without_redis_and_sends_only_a_safe_capacity_command(
    monkeypatch, reason, seconds, expected, redis_failure
):
    settings = configuration(
        ai_capacity_cooldown_seconds=300, ai_server_overload_cooldown_seconds=30
    )
    monkeypatch.setattr(ai_capacity, "get_settings", lambda: settings)
    connection = Mock()
    connect = Mock(return_value=nullcontext(connection))
    if redis_failure:
        connect.side_effect = RedisConnectionError("private-redis-token")
    monkeypatch.setattr(ai_capacity, "create_redis", connect)
    assert ai_capacity.safe_pause(seconds, reason=reason) == expected
    assert connect.call_args.kwargs == {"socket_connect_timeout": 3, "socket_timeout": 3}
    if redis_failure:
        connection.eval.assert_not_called()
    else:
        arguments = connection.eval.call_args.args
        assert arguments[1:] == (1, ai_capacity.cooldown_key(), expected)
        assert settings.codex_app_server_url not in arguments[2] and reason not in str(arguments)
        assert "private" not in str(arguments)


@pytest.mark.parametrize("failure", ["registry-limit", "redis"])
def test_unavailable_worker_observation_uses_bounded_admission_without_claiming_idle_workers(
    monkeypatch, failure
):
    connection = MagicMock()
    connection.smembers.return_value = {f"rq:worker:{index}".encode() for index in range(1001)}
    connect = Mock(return_value=nullcontext(connection))
    if failure == "redis":
        connect.side_effect = RedisConnectionError("private-worker-registry")
    monkeypatch.setattr(pipeline_capacity, "create_redis", connect)
    monkeypatch.setattr(
        pipeline_capacity, "get_settings", lambda: configuration(topic_decision_max_pending=2)
    )
    observed = pipeline_capacity.observe_capacity()
    assert observed == {"observed": False}
    assert pipeline_capacity.topic_admission_limit(observed) == 2
    connection.pipeline.assert_not_called()
    assert connect.call_args.kwargs["socket_connect_timeout"] == 0.2
    assert connect.call_args.kwargs["socket_timeout"] == 0.2


def test_invalid_or_stale_heartbeat_cannot_create_observed_worker_capacity(monkeypatch):
    connection, pipe = MagicMock(), MagicMock()
    connection.smembers.return_value = {b"rq:worker:first", b"rq:worker:second"}
    connection.ttl.return_value = 0
    connection.pipeline.return_value.__enter__.return_value = pipe
    pipe.execute.return_value = [
        [b"topic-analysis", b"idle", b"", b"invalid-heartbeat"],
        300,
        [b"article-analysis", b"idle", b"", (NOW - timedelta(seconds=121)).isoformat().encode()],
        300,
    ]
    monkeypatch.setattr(pipeline_capacity, "utcnow", lambda: NOW)
    result = pipeline_capacity.worker_capacity(connection)
    assert result["observed"] is True and result["cooldown_seconds"] == 0
    assert all(value == 0 for key, value in result.items() if key != "observed")
