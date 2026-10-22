"""Retry orchestration and supported Redis client options, without external services."""

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import pipeline_capacity, transaction_retry
from devfeed_core import redis as connections
from psycopg.errors import DeadlockDetected, SerializationFailure, UniqueViolation
from sqlalchemy.exc import DBAPIError
from test_redis_connections import settings
from test_source_readiness_unit import NOW


@pytest.mark.parametrize("driver_error", [DeadlockDetected, SerializationFailure])
@pytest.mark.parametrize("eventual_success", [True, False])
def test_retryable_transaction_closes_each_scope_before_backoff_or_propagating_exhaustion(
    monkeypatch, driver_error, eventual_success
):
    events, sessions = [], [object(), object(), object()]
    errors = [DBAPIError("private statement", {}, driver_error("conflict")) for _ in sessions]

    @contextmanager
    def begin():
        index = sum(event[0] == "begin" for event in events)
        events.append(("begin", index))
        try:
            yield sessions[index]
        finally:
            events.append(("close", index))

    def operation(session):
        index = sessions.index(session)
        events.append(("operation", index))
        if eventual_success and index == 2:
            return "committed result"
        raise errors[index]

    monkeypatch.setattr(transaction_retry, "random", SimpleNamespace(uniform=lambda *_: 0.03))
    monkeypatch.setattr(
        transaction_retry,
        "time",
        SimpleNamespace(sleep=lambda delay: events.append(("sleep", delay))),
    )
    factory = SimpleNamespace(begin=begin)
    if eventual_success:
        assert transaction_retry.run_transaction(factory, operation) == "committed result"
    else:
        with pytest.raises(DBAPIError) as raised:
            transaction_retry.run_transaction(factory, operation)
        assert raised.value is errors[-1]
    assert events == [
        ("begin", 0),
        ("operation", 0),
        ("close", 0),
        ("sleep", 0.03),
        ("begin", 1),
        ("operation", 1),
        ("close", 1),
        ("sleep", 0.06),
        ("begin", 2),
        ("operation", 2),
        ("close", 2),
    ]


@pytest.mark.parametrize("database_failure", [True, False])
def test_nontransient_transaction_failure_is_never_replayed(monkeypatch, database_failure):
    error = (
        DBAPIError("private statement", {}, UniqueViolation("duplicate key"))
        if database_failure
        else ValueError("invalid domain command")
    )
    factory = MagicMock()
    operation = Mock(side_effect=error)
    pause = Mock()
    monkeypatch.setattr(transaction_retry, "time", SimpleNamespace(sleep=pause))
    with pytest.raises(type(error)) as raised:
        transaction_retry.run_transaction(factory, operation)
    assert raised.value is error
    factory.begin.assert_called_once_with()
    operation.assert_called_once()
    factory.begin.return_value.__exit__.assert_called_once()
    pause.assert_not_called()


@pytest.mark.parametrize("sentinel", [False, True])
def test_supported_older_redis_client_receives_no_unsupported_maintenance_option(
    monkeypatch, sentinel
):
    # redis>=5.2,<9 is supported; the maintenance module arrived only in 6.4.
    monkeypatch.setitem(sys.modules, "redis.maint_notifications", None)
    direct, discovery = Mock(), Mock()
    monkeypatch.setattr(connections.Redis, "from_url", direct)
    monkeypatch.setattr(connections, "Sentinel", discovery)
    config = settings(
        **(
            {"redis_sentinel_nodes": [("sentinel", 26379)], "redis_sentinel_master": "valkey"}
            if sentinel
            else {}
        )
    )
    result = connections.create_redis(config, socket_timeout=0.25)
    if sentinel:
        assert result is discovery.return_value.master_for.return_value
        direct.assert_not_called()
        options = discovery.return_value.master_for.call_args.kwargs
        discovery_options = discovery.call_args.kwargs["sentinel_kwargs"]
        assert discovery.call_args.args == ([("sentinel", 26379)],)
        assert discovery.return_value.master_for.call_args.args == ("valkey",)
        assert options["redis_class"] is connections._SentinelRedis
        assert options["db"] == 10 and "host" not in options and "port" not in options
        assert "maint_notifications_config" not in discovery_options
        assert discovery_options["retry"] is options["retry"]
        assert discovery_options["socket_timeout"] == 0.25
    else:
        assert result is direct.return_value
        discovery.assert_not_called()
        assert direct.call_args.args == (config.redis_url,)
        options = direct.call_args.kwargs
    assert "maint_notifications_config" not in options
    assert options["socket_connect_timeout"] == 3 and options["socket_timeout"] == 0.25
    assert options["retry"].get_retries() == 3


@pytest.mark.parametrize(
    "ttl,state,death,heartbeat",
    [
        (0, b"idle", None, NOW.isoformat().encode()),
        (-2, b"idle", None, NOW.isoformat().encode()),
        (300, b"idle", NOW.isoformat().encode(), NOW.isoformat().encode()),
        (300, b"suspended", None, NOW.isoformat().encode()),
        (300, b"idle", None, None),
    ],
)
def test_expired_dead_unavailable_or_unobserved_worker_cannot_authorize_admission(
    monkeypatch, ttl, state, death, heartbeat
):
    # Supply a Redis response, not an implementation of its registry or TTL semantics.
    connection = Mock()
    pipe = Mock()
    connection.smembers.return_value = {b"rq:worker:observed"}
    connection.pipeline.return_value.__enter__ = Mock(return_value=pipe)
    connection.pipeline.return_value.__exit__ = Mock(return_value=False)
    pipe.execute.return_value = [[b"topic-analysis,article-analysis", state, death, heartbeat], ttl]
    monkeypatch.setattr(pipeline_capacity, "cooldown_remaining", lambda _: 0)
    monkeypatch.setattr(pipeline_capacity, "utcnow", lambda: NOW)
    result = pipeline_capacity.worker_capacity(connection)
    assert result["observed"] is True
    assert all(value == 0 for key, value in result.items() if key != "observed")
    connection.pipeline.assert_called_once_with(transaction=False)
    pipe.hmget.assert_called_once_with(
        b"rq:worker:observed", "queues", "state", "death", "last_heartbeat"
    )
    pipe.ttl.assert_called_once_with(b"rq:worker:observed")
