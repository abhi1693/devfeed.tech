"""Real RQ construction and slow Redis responses retain bounded control I/O."""

import socketserver
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import analysis_worker
from devfeed_aggregator.analysis_worker import AnalysisAwareWorker, BoundedDequeueQueue
from devfeed_aggregator.queue import get_queue
from devfeed_core.config import get_settings
from redis import Redis
from rq import Queue
from rq.exceptions import DequeueTimeout
from rq.job import Job


@pytest.mark.parametrize("previous", [None, 85, 0.025])
def test_rq_construction_preserves_short_control_timeouts_and_disables_retries(previous):
    connection = Redis(host="redis.invalid", socket_timeout=previous)
    try:
        worker = AnalysisAwareWorker(
            [Queue("images", connection=connection)],
            connection=connection,
            prepare_for_work=False,
            worker_ttl=90,
        )
        assert worker.connection is connection
        options = connection.connection_pool.connection_kwargs
        assert options["socket_timeout"] == (0.025 if previous == 0.025 else 5)
        assert options["socket_connect_timeout"] <= 5
        assert options["retry"].get_retries() == 0
        assert worker.queue_class is BoundedDequeueQueue
        assert worker.connection_timeout == 85  # RQ's long default is never installed.
    finally:
        connection.close()


@pytest.mark.parametrize("operation", ["lmove", "lpop"])
def test_blocking_pop_has_an_independent_bounded_socket_and_closes_it(monkeypatch, operation):
    waiting = Redis(host="redis.invalid", socket_timeout=85)
    blocking = Mock(return_value=b"job" if operation == "lmove" else (b"rq:queue:low", b"job"))
    monkeypatch.setattr(waiting, "b" + operation, blocking)
    closed = Mock(wraps=waiting.close)
    monkeypatch.setattr(waiting, "close", closed)
    factory = Mock(return_value=waiting)
    monkeypatch.setattr(analysis_worker, "create_redis", factory)
    with Redis(host="redis.invalid", socket_timeout=0.025) as control:
        if operation == "lmove":
            assert BoundedDequeueQueue.lmove(control, "rq:queue:low", 75)[1] == b"job"
        else:
            assert BoundedDequeueQueue.lpop(["rq:queue:low"], 75, control)[1] == b"job"
        assert control.connection_pool.connection_kwargs["socket_timeout"] == 0.025
    assert blocking.call_args.args[-1] == 10
    assert waiting.connection_pool.connection_kwargs["socket_timeout"] == 11
    assert waiting.connection_pool.connection_kwargs["retry"].get_retries() == 0
    closed.assert_called_once_with()


@pytest.mark.parametrize("operation", ["lmove", "lpop"])
def test_nonblocking_pop_uses_only_the_control_client(monkeypatch, operation):
    factory = Mock(side_effect=AssertionError("Nonblocking pops need no waiting client"))
    monkeypatch.setattr(analysis_worker, "create_redis", factory)
    with Redis(host="redis.invalid") as control:
        command = Mock(return_value=b"delivery")
        monkeypatch.setattr(control, operation, command)
        if operation == "lmove":
            assert BoundedDequeueQueue.lmove(control, "rq:queue:low", None)[1] == b"delivery"
        else:
            assert BoundedDequeueQueue.lpop(["rq:queue:low"], None, control)[1] == b"delivery"
        command.assert_called_once()
    factory.assert_not_called()


def test_explicit_custom_queue_backend_is_preserved():
    class CustomQueue(Queue):
        pass

    with Redis(host="redis.invalid") as control:
        worker = AnalysisAwareWorker(
            [CustomQueue("images", connection=control)],
            connection=control,
            queue_class=CustomQueue,
            prepare_for_work=False,
        )
        assert worker.queue_class is CustomQueue


def test_rq_construction_bounds_sentinel_data_and_discovery_pools(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "redis_sentinel_nodes", [("sentinel.invalid", 26379)])
    monkeypatch.setattr(settings, "redis_sentinel_master", "test-primary")
    queue = get_queue("images")
    try:
        worker = AnalysisAwareWorker([queue], connection=queue.connection, prepare_for_work=False)
        clients = [worker.connection, *worker.connection.connection_pool.sentinel_manager.sentinels]
        for client in clients:
            options = client.connection_pool.connection_kwargs
            assert options["socket_timeout"] <= 5
            assert options["socket_connect_timeout"] <= 5
            assert options["retry"].get_retries() == 0
    finally:
        queue.connection.close()


def test_rq_fetch_keeps_using_control_client_after_blocking_pop(monkeypatch):
    with Redis(host="redis.invalid", socket_timeout=0.025) as control:
        monkeypatch.setattr("rq.queue.get_version", lambda _connection: (8, 0, 0))
        monkeypatch.setattr(
            BoundedDequeueQueue,
            "lmove",
            classmethod(lambda cls, connection, key, timeout: (key, b"delivery")),
        )
        delivery = SimpleNamespace(id="delivery")
        fetch = Mock(return_value=delivery)
        monkeypatch.setattr(Job, "fetch", fetch)
        result = BoundedDequeueQueue.dequeue_any(
            [Queue("low", connection=control)], 10, connection=control
        )
        assert result[0] is delivery and result[1].connection is control
        assert fetch.call_args.kwargs["connection"] is control


@pytest.fixture
def slow_redis(monkeypatch):
    """A local RESP2 peer can stall commands without production Redis or forks."""
    release, received = threading.Event(), threading.Event()
    state = SimpleNamespace(commands=[], stall=b"HGETALL", delivery=None)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            while line := self.rfile.readline():
                parts = []
                for _ in range(int(line[1:])):
                    size = int(self.rfile.readline()[1:])
                    parts.append(self.rfile.read(size))
                    self.rfile.read(2)
                command = parts[0].upper()
                state.commands.append(command)
                if command == state.stall:
                    received.set()
                    release.wait(2)
                    return
                if command in {b"CLIENT", b"SELECT"}:
                    response = b"+OK\r\n"
                elif command == b"INFO":
                    body = b"redis_version:8.0.0\r\n"
                    response = b"$" + str(len(body)).encode() + b"\r\n" + body + b"\r\n"
                elif command == b"BLMOVE":
                    if state.delivery is None:
                        release.wait(float(parts[-1]))
                        response = b"$-1\r\n"
                    else:
                        response = b"$" + str(len(state.delivery)).encode()
                        response += b"\r\n" + state.delivery + b"\r\n"
                else:
                    response = b":1\r\n"
                self.wfile.write(response)
                self.wfile.flush()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
        thread.start()
        url = f"redis://127.0.0.1:{server.server_address[1]}/15?protocol=2"
        monkeypatch.setattr(get_settings(), "redis_url", url)
        monkeypatch.setattr(get_settings(), "job_queue_overrides", {})
        monkeypatch.setattr(analysis_worker, "CONTROL_TIMEOUT_SECONDS", 0.05)
        state.received = received
        try:
            yield state
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=2)


def test_slow_redis_exchange_observes_stop_without_retries_or_false_health(slow_redis):
    slow_redis.stall = b"LLEN"
    queue = get_queue("default", physical=True)
    worker = AnalysisAwareWorker([queue], connection=queue.connection, prepare_for_work=False)
    worker.write_health = Mock()

    def stop():
        if slow_redis.received.wait(1):
            worker._shutdown()

    stopper = threading.Thread(target=stop)
    stopper.start()
    started = time.monotonic()
    try:
        assert worker.dequeue_job_and_maintain_ttl(75, max_idle_time=1) is None
        assert time.monotonic() - started < 1
        assert worker._stop_requested
        assert slow_redis.commands.count(b"LLEN") == 1
        worker.write_health.assert_not_called()
    finally:
        queue.connection.close()
        stopper.join(timeout=2)


def test_intentional_blocking_wait_can_exceed_control_socket_timeout(slow_redis):
    queue = get_queue("low", physical=True)
    worker = AnalysisAwareWorker([queue], connection=queue.connection, prepare_for_work=False)
    started = time.monotonic()
    try:
        with pytest.raises(DequeueTimeout):
            worker.queue_class.lmove(worker.connection, queue.key, 0.15)
        elapsed = time.monotonic() - started
        assert 0.1 <= elapsed < 1
        assert worker.connection.connection_pool.connection_kwargs["socket_timeout"] == 0.05
        assert slow_redis.commands.count(b"BLMOVE") == 1
    finally:
        queue.connection.close()


def test_slow_hash_fetch_after_blocking_pop_uses_short_control_socket(slow_redis):
    slow_redis.delivery = b"delivery"
    queue = get_queue("low", physical=True)
    worker = AnalysisAwareWorker([queue], connection=queue.connection, prepare_for_work=False)
    started = time.monotonic()
    try:
        from redis.exceptions import TimeoutError

        with pytest.raises(TimeoutError):
            worker.queue_class.dequeue_any([queue], 0.15, connection=worker.connection)
        assert time.monotonic() - started < 1
        assert slow_redis.commands.count(b"HGETALL") == 1
        assert slow_redis.commands.count(b"BLMOVE") == 1
    finally:
        queue.connection.close()
