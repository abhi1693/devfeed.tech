"""Feed dispatch continues independently of the main scheduler's long-running work."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

from devfeed_aggregator.recommendation_dispatcher import recommendation_dispatcher


def test_dispatcher_retries_failure_and_stops_with_scheduler(monkeypatch):
    completed = threading.Event()
    calls, closed = [], []

    def dispatch(factory, queue):
        calls.append(factory)
        if len(calls) == 1:
            raise RuntimeError("temporary queue outage")
        completed.set()
        return 1

    monkeypatch.setattr(
        "devfeed_aggregator.recommendation_dispatcher.dispatch_recommendations", dispatch
    )
    queue = SimpleNamespace(connection=SimpleNamespace(close=lambda: closed.append(True)))
    lookup = Mock(return_value=queue)
    with recommendation_dispatcher("factory", lookup, interval=0.01):
        assert completed.wait(timeout=2)
    assert len(calls) >= 2
    assert closed == [True]
    lookup.assert_called_once_with()
    assert not any(t.name == "recommendation-dispatch" for t in threading.enumerate())


def test_dispatcher_retries_client_creation_and_closes_only_its_owned_client(monkeypatch):
    completed = threading.Event()
    connection = SimpleNamespace(close=Mock())
    queue = SimpleNamespace(connection=connection)
    lookup = Mock(side_effect=[RuntimeError("Sentinel unavailable"), queue])

    def dispatch(factory, actual):
        assert factory == "factory" and actual is queue
        completed.set()
        return 0

    monkeypatch.setattr(
        "devfeed_aggregator.recommendation_dispatcher.dispatch_recommendations", dispatch
    )
    with recommendation_dispatcher("factory", lookup, interval=0.01):
        assert completed.wait(timeout=2)
    assert lookup.call_count == 2
    connection.close.assert_called_once_with()
