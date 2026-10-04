"""Scheduler isolation, queue cleanup, recovery routing, and heartbeat safety."""

import logging
import sys
import uuid
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_aggregator import quota_monitor, scheduler
from devfeed_core import feed_notifications, job_retention
from devfeed_notifications import delivery

NOW = datetime(2026, 10, 4, tzinfo=UTC)


@pytest.mark.parametrize("batch", [1, 5, 10])
@pytest.mark.parametrize("fail", [False, True])
def test_article_lanes_reserve_capacity_and_close_connections_on_failure(monkeypatch, batch, fail):
    queues = [Mock(), Mock()]
    lookup = Mock(side_effect=queues)
    dispatch = Mock(side_effect=RuntimeError("queue unavailable")) if fail else Mock(return_value=2)
    monkeypatch.setattr(scheduler, "get_queue", lookup)
    monkeypatch.setattr(scheduler, "dispatch_jobs", dispatch)
    if fail:
        with pytest.raises(RuntimeError):
            scheduler.dispatch_article_lanes("factory", "analysis", batch, NOW)
    else:
        assert scheduler.dispatch_article_lanes("factory", "analysis", batch, NOW) == 4
        assert [call.args[0] for call in lookup.call_args_list] == [
            "article-analysis-fresh",
            "article-analysis",
        ]
    assert dispatch.call_args.args[2] == max(1, batch // 2)
    queues[0].connection.close.assert_called_once()
    assert queues[1].connection.close.call_count == int(not fail)


@pytest.mark.parametrize(
    "field,level",
    [
        (None, logging.DEBUG),
        ("scheduled", logging.INFO),
        ("recovered", logging.WARNING),
        ("images_recovered", logging.WARNING),
        ("profiles_recovered", logging.WARNING),
        ("articles_recovered", logging.WARNING),
    ],
)
def test_tick_logs_work_and_recovery_at_the_correct_severity(monkeypatch, caplog, field, level):
    counts = {"recovered": 0, "images_recovered": 0, "scheduled": 0}
    if field:
        counts[field] = 1
    monkeypatch.setattr(scheduler, "_tick", lambda: counts)
    monkeypatch.setattr(scheduler, "background_cycle", lambda _: nullcontext())
    with caplog.at_level(logging.DEBUG, logger=scheduler.__name__):
        assert scheduler.tick() == counts
    record = next(
        record for record in caplog.records if record.message == "scheduler_tick_completed"
    )
    assert record.levelno == level and record.duration_ms >= 0


def test_failed_tick_is_logged_and_propagates(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "_tick", Mock(side_effect=RuntimeError("tick failed")))
    monkeypatch.setattr(scheduler, "background_cycle", lambda _: nullcontext())
    with pytest.raises(RuntimeError), caplog.at_level(logging.ERROR):
        scheduler.tick()
    assert any(record.message == "scheduler_tick_failed" for record in caplog.records)


@pytest.mark.parametrize(
    "kind",
    [
        "analysis",
        "topic-analysis",
        "research-verification",
        "images",
        "source-enrichment",
        "article-enrichment",
    ],
)
def test_expired_jobs_use_their_pipeline_recovery_policy(monkeypatch, kind):
    job = NS(id=uuid.UUID(int=1), status="running")
    session, factory = Mock(), Mock()
    session.scalars.return_value.all.return_value = [job]
    factory.begin.return_value = nullcontext(session)
    handlers = {name: Mock() for name in ("fail_analysis", "fail_verification", "fail_or_retry")}
    for name, handler in handlers.items():
        monkeypatch.setattr(scheduler, name, handler)
    assert scheduler.recover_jobs(factory, 10, NOW, kind=kind) == 1
    selected = (
        "fail_verification"
        if kind == "research-verification"
        else "fail_analysis"
        if kind in {"analysis", "topic-analysis"}
        else "fail_or_retry"
    )
    assert {name: handler.call_count for name, handler in handlers.items()} == {
        name: int(name == selected) for name in handlers
    }
    assert handlers[selected].call_args.args[0] is job


def test_unsupported_recovery_requires_an_explicit_policy():
    factory = Mock()
    with pytest.raises(ValueError, match="own recovery policy"):
        scheduler.recover_jobs(factory, 10, NOW, kind="ingestion")
    factory.begin.assert_not_called()


@pytest.mark.parametrize(
    "mode",
    [
        "minimal",
        "all",
        "retention-failed",
        "overview-failed",
        "dispatch-failed",
        "topics-only",
        "relationships-only",
    ],
)
def test_tick_dispatches_outside_transactions_and_preserves_cleanup(monkeypatch, mode):
    session, factory = Mock(), Mock()
    opened = 0

    @contextmanager
    def transaction():
        nonlocal opened
        opened += 1
        try:
            yield session
        finally:
            opened -= 1

    factory.begin.side_effect = transaction
    expired = NS(id=uuid.UUID(int=2), source_id=uuid.UUID(int=3), status="running", attempts=1)
    source = NS(id=uuid.UUID(int=4))
    session.scalars.side_effect = [
        NS(all=lambda: [uuid.UUID(int=1), expired.id]),
        NS(all=lambda: [source]),
    ]
    session.scalar.side_effect = [None, expired]
    settings = NS(
        scheduler_batch_size=5,
        notifications_enabled=mode == "all",
        solver_queue_enabled=mode == "all",
        ai_enabled=mode != "minimal",
        auto_approve_topics=mode in {"all", "topics-only"},
        auto_approve_topic_relationships=mode in {"all", "relationships-only"},
    )
    monkeypatch.setattr(scheduler, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler, "session_factory", lambda: factory)
    monkeypatch.setattr(scheduler, "utcnow", lambda: NOW)
    monkeypatch.setattr(quota_monitor, "refresh_quota", Mock())
    refresh = Mock(side_effect=RuntimeError("overview")) if mode == "overview-failed" else Mock()
    monkeypatch.setattr(scheduler, "refresh_overview_daily", refresh)
    prune = Mock()
    monkeypatch.setattr(scheduler, "prune_article_opens", prune)
    monkeypatch.setattr(
        job_retention,
        "prune_job_payloads",
        Mock(side_effect=RuntimeError("retention")) if mode == "retention-failed" else Mock(),
    )
    monkeypatch.setattr(scheduler, "expand_recommendation_events", Mock(return_value=3))
    monkeypatch.setattr(
        scheduler, "schedule_automation", Mock(return_value={"automation_scheduled": 4})
    )
    monkeypatch.setattr(scheduler, "schedule_verification", Mock(return_value=2))
    monkeypatch.setattr(scheduler, "fail_job", Mock())
    monkeypatch.setattr(scheduler, "request_ingestion", Mock(return_value=NS(id=uuid.UUID(int=5))))
    monkeypatch.setattr(scheduler, "recover_jobs", Mock(return_value=1))
    monkeypatch.setattr(feed_notifications, "expand_feed_notifications", Mock())
    monkeypatch.setattr(delivery, "recover_notifications", Mock(return_value=2))
    monkeypatch.setattr(scheduler, "dispatch_discovery", Mock())
    monkeypatch.setattr(scheduler, "dispatch_recommendations", Mock(return_value=3))
    queues = []

    def queue(*_):
        result = Mock()
        queues.append(result)
        return result

    def dispatch(*_, **kwargs):
        assert opened == 0, "Redis dispatch must not hold database transactions"
        if mode == "dispatch-failed":
            raise RuntimeError("dispatch")
        return 1

    def lanes(_, __, ___, ____, definitions):
        assert opened == 0
        return {lane.kind: 1 for lane in definitions}

    monkeypatch.setattr(scheduler, "get_queue", queue)
    monkeypatch.setattr(scheduler, "dispatch_jobs", dispatch)
    monkeypatch.setattr(scheduler, "dispatch_lanes", lanes)
    if mode == "dispatch-failed":
        with pytest.raises(RuntimeError, match="dispatch"):
            scheduler._tick()
    else:
        result = scheduler._tick()
        assert result["recovered"] == result["scheduled"] == result["dispatched"] == 1
        assert result["automation_scheduled"] == 4 and result["recommendation_users_queued"] == 3
        assert result["analyses_dispatched"] == (0 if mode == "minimal" else 2)
        assert result["notifications_dispatched"] == int(mode == "all")
        assert result["notifications_recovered"] == (2 if mode == "all" else 0)
        assert result["verifications_dispatched"] == int(
            mode in {"all", "topics-only", "relationships-only"}
        )
        heartbeat_queue = next(item for item in queues if item.connection.set.call_count)
        heartbeat_queue.connection.set.assert_called_once_with(
            "devfeed:scheduler:heartbeat", NOW.isoformat(), ex=120
        )
    assert prune.call_count == int(mode != "overview-failed")
    assert all(item.connection.close.call_count == 1 for item in queues)


@pytest.mark.parametrize("fails", [False, True])
def test_scheduler_heartbeat_only_advances_after_successful_cycles(monkeypatch, fails):
    stop, health, telemetry = Mock(), Mock(), object()
    stop.is_set.side_effect = [False, True]
    health_context = Mock()
    health_context.return_value = nullcontext(health)
    monkeypatch.setattr(scheduler, "get_settings", lambda: NS(log_level="INFO", log_format="json"))
    monkeypatch.setattr(scheduler, "configure_logging", Mock())
    monkeypatch.setattr(scheduler, "start_runtime", lambda _: telemetry)
    finish = Mock()
    monkeypatch.setattr(scheduler, "stop_runtime", finish)
    monkeypatch.setattr(scheduler.threading, "Event", lambda: stop)
    signal_handler = Mock()
    monkeypatch.setattr(scheduler.signal, "signal", signal_handler)
    monkeypatch.setattr(scheduler, "scheduler_health", health_context)
    monkeypatch.setattr(scheduler, "session_factory", Mock())
    monkeypatch.setattr(scheduler, "recommendation_dispatcher", lambda *_: nullcontext())
    monkeypatch.setattr(
        scheduler, "tick", Mock(side_effect=RuntimeError("tick")) if fails else Mock()
    )
    scheduler.run()
    assert health.completed.call_count == int(not fails)
    stop.wait.assert_called_once_with(15)
    finish.assert_called_once_with(telemetry)
    signal_handler.call_args.args[1](None, None)
    stop.set.assert_called_once()


@pytest.mark.parametrize("mode", ["once", "failed", "daemon"])
def test_scheduler_cli_reports_failed_once_runs(monkeypatch, mode):
    monkeypatch.setattr(sys, "argv", ["scheduler"] + ([] if mode == "daemon" else ["--once"]))
    monkeypatch.setattr(scheduler, "get_settings", lambda: NS(log_level="INFO", log_format="json"))
    monkeypatch.setattr(scheduler, "configure_logging", Mock())
    tick, run = Mock(side_effect=RuntimeError("tick")) if mode == "failed" else Mock(), Mock()
    monkeypatch.setattr(scheduler, "tick", tick)
    monkeypatch.setattr(scheduler, "run", run)
    if mode == "failed":
        with pytest.raises(SystemExit) as exc:
            scheduler.main()
        assert exc.value.code == 1
    else:
        scheduler.main()
    assert run.call_count == int(mode == "daemon") and tick.call_count == int(mode != "daemon")
