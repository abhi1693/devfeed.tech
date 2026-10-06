"""A producer failure must not stop the shared browser notification outbox."""

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from devfeed_aggregator import quota_monitor, scheduler
from devfeed_core import job_retention


def test_failed_daily_producer_still_expands_recovers_and_dispatches_shared_push(monkeypatch):
    settings = SimpleNamespace(
        scheduler_batch_size=10,
        web_push_enabled=True,
        notifications_enabled=False,
        solver_queue_enabled=False,
        ai_enabled=False,
    )
    session, factory, queue = Mock(), Mock(), Mock()
    session.scalars.return_value.all.return_value = []
    factory.begin.return_value = nullcontext(session)
    now = datetime(2026, 10, 6, 9, tzinfo=UTC)
    monkeypatch.setattr(scheduler, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler, "session_factory", lambda: factory)
    monkeypatch.setattr(scheduler, "get_queue", lambda *args: queue)
    monkeypatch.setattr(scheduler, "utcnow", lambda: now)
    monkeypatch.setattr(quota_monitor, "refresh_quota", Mock())
    monkeypatch.setattr(job_retention, "prune_job_payloads", Mock())
    for name in (
        "refresh_overview_daily",
        "prune_article_opens",
        "dispatch_discovery",
    ):
        monkeypatch.setattr(scheduler, name, Mock())
    monkeypatch.setattr(scheduler, "expand_recommendation_events", Mock(return_value=0))
    monkeypatch.setattr(scheduler, "schedule_automation", Mock(return_value={}))
    monkeypatch.setattr(scheduler, "schedule_verification", Mock(return_value=0))
    monkeypatch.setattr(scheduler, "recover_jobs", Mock(return_value=0))
    monkeypatch.setattr(scheduler, "dispatch_recommendations", Mock(return_value=0))
    monkeypatch.setattr(
        scheduler, "schedule_daily_pushes", Mock(side_effect=RuntimeError("daily producer failed"))
    )
    expand, recover, dispatch = Mock(return_value=3), Mock(return_value=1), Mock(return_value=2)
    monkeypatch.setattr(scheduler, "expand_web_push_events", expand)
    monkeypatch.setattr(scheduler, "recover_web_push", recover)
    monkeypatch.setattr(scheduler, "dispatch_jobs", dispatch)
    result = scheduler._tick()
    assert result["pushes_scheduled"] == 0
    assert result["push_recipients_expanded"] == 3
    assert result["pushes_recovered"] == 1 and result["pushes_dispatched"] == 2
    expand.assert_called_once_with(factory, 10, now=now)
    recover.assert_called_once_with(factory, 10, now)
    assert any(call.kwargs.get("kind") == "web-push" for call in dispatch.call_args_list)
    assert result["dispatched"] == 2
