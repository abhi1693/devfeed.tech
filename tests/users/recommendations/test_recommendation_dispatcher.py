"""Feed dispatch continues independently of the main scheduler's long-running work."""

import threading
from types import SimpleNamespace

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
    with recommendation_dispatcher("factory", lambda: queue, interval=0.01):
        assert completed.wait(timeout=2)
    assert len(calls) >= 2
    assert len(closed) == len(calls)
    assert not any(t.name == "recommendation-dispatch" for t in threading.enumerate())
