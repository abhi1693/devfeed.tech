"""Partner refill stays fair and isolated from the scheduler's long-running work."""

import logging
import threading
from unittest.mock import Mock

import pytest
from devfeed_aggregator import partner_dispatcher as dispatcher


def test_refill_alternates_shared_queue_access_and_skips_source_scheduling(monkeypatch):
    completed = threading.Event()
    calls = []

    def pipeline(factory, *, schedule):
        assert factory == "factory"
        assert schedule is False
        calls.append("pipeline")
        if len(calls) == 4:
            completed.set()
        return 1

    def evaluations(factory):
        assert factory == "factory"
        calls.append("evaluation")
        return 1

    monkeypatch.setattr(dispatcher, "dispatch_partner_pipeline", pipeline)
    monkeypatch.setattr(dispatcher, "dispatch_partner_evaluations", evaluations)
    with dispatcher.partner_dispatcher("factory", interval=0.01):
        assert completed.wait(timeout=2)

    assert calls[:4] == ["pipeline", "evaluation", "evaluation", "pipeline"]
    assert not any(thread.name == "partner-dispatch" for thread in threading.enumerate())


@pytest.mark.parametrize("failed", ["pipeline", "evaluation"])
def test_failed_refill_does_not_stop_the_other_operation(monkeypatch, caplog, failed):
    completed = threading.Event()
    calls = []

    def dispatch(operation):
        calls.append(operation)
        if len(calls) == 4:
            completed.set()
        if operation == failed and calls.count(operation) == 1:
            raise RuntimeError("credential-bearing upstream failure")
        return 1

    monkeypatch.setattr(
        dispatcher, "dispatch_partner_pipeline", lambda *_args, **_kwargs: dispatch("pipeline")
    )
    monkeypatch.setattr(
        dispatcher, "dispatch_partner_evaluations", lambda *_: dispatch("evaluation")
    )
    with (
        caplog.at_level(logging.WARNING, logger=dispatcher.__name__),
        dispatcher.partner_dispatcher("factory", interval=0.01),
    ):
        assert completed.wait(timeout=2)

    assert calls[:4] == ["pipeline", "evaluation", "evaluation", "pipeline"]
    failure = next(
        record
        for record in caplog.records
        if record.message == "partner_" + failed + "_refill_failed"
    )
    assert failure.error_type == "RuntimeError"
    assert failure.exc_info is None
    assert "credential-bearing" not in caplog.text
    assert not any(thread.name == "partner-dispatch" for thread in threading.enumerate())


def test_refill_stops_when_scheduler_context_fails(monkeypatch):
    entered = threading.Event()
    monkeypatch.setattr(dispatcher, "dispatch_partner_pipeline", lambda *_a, **_kw: entered.set())
    monkeypatch.setattr(dispatcher, "dispatch_partner_evaluations", Mock(return_value=0))

    with (
        pytest.raises(ValueError, match="scheduler failed"),
        dispatcher.partner_dispatcher("factory", interval=60),
    ):
        assert entered.wait(timeout=2)
        raise ValueError("scheduler failed")

    assert not any(thread.name == "partner-dispatch" for thread in threading.enumerate())


def test_refill_shutdown_uses_a_bounded_join(monkeypatch):
    stop = Mock()
    thread = Mock()
    monkeypatch.setattr(dispatcher.threading, "Event", lambda: stop)
    constructor = Mock(return_value=thread)
    monkeypatch.setattr(dispatcher.threading, "Thread", constructor)

    with dispatcher.partner_dispatcher("factory"):
        thread.start.assert_called_once()
        stop.set.assert_not_called()

    assert constructor.call_args.kwargs["name"] == "partner-dispatch"
    assert constructor.call_args.kwargs["daemon"] is True
    stop.set.assert_called_once()
    thread.join.assert_called_once_with(timeout=5)
