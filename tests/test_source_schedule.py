import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_core import jobs, services
from devfeed_core.models import Source
from devfeed_core.schemas import SourcePatch

NOW = datetime(2026, 10, 9, 1, tzinfo=UTC)
SUCCESS = NOW - timedelta(hours=1)


def source(**changes):
    fields = dict(
        id=uuid.uuid4(),
        name="Example",
        feed_url="https://example.com/rss",
        source_type="publisher",
        enabled=True,
        approval_status="approved",
        poll_interval_seconds=43200,
        last_success_at=SUCCESS,
        next_fetch_at=SUCCESS + timedelta(seconds=43200),
        consecutive_failures=0,
    )
    return Source(**(fields | changes))


def update(monkeypatch, record, patch, active=None):
    statements = []
    rows = iter([record, active])

    def scalar(statement):
        statements.append(statement)
        return next(rows)

    session = SimpleNamespace(scalar=scalar, flush=lambda: None)
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    assert services.update_source(session, record.id, SourcePatch(**patch)) is record
    assert statements[0]._for_update_arg is not None
    return statements


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [(43200, 300, NOW), (300, 43200, SUCCESS + timedelta(hours=12))],
)
def test_interval_changes_reconcile_cadence(monkeypatch, old, new, expected):
    record = source(poll_interval_seconds=old, next_fetch_at=SUCCESS + timedelta(seconds=old))
    update(monkeypatch, record, {"poll_interval_seconds": new})
    assert record.poll_interval_seconds == new
    assert record.next_fetch_at == expected


def test_future_shortened_cadence_uses_last_success_not_edit_time(monkeypatch):
    record = source(last_success_at=NOW - timedelta(seconds=60))
    update(monkeypatch, record, {"poll_interval_seconds": 300})
    assert record.next_fetch_at == NOW + timedelta(seconds=240)


@pytest.mark.parametrize(
    "patch", [{"name": "Renamed"}, {"poll_interval_seconds": 43200}, {"enabled": True}]
)
def test_unchanged_interval_and_unrelated_edits_keep_schedule(monkeypatch, patch):
    record = source()
    previous = record.next_fetch_at
    assert len(update(monkeypatch, record, patch)) == 1
    assert record.next_fetch_at == previous


@pytest.mark.parametrize(
    "fields",
    [
        {"last_success_at": None},
        {"enabled": False},
        {"approval_status": "pending"},
        {"approval_status": "rejected"},
        {"consecutive_failures": 1, "last_error": "HTTP 429 Retry-After"},
    ],
)
@pytest.mark.parametrize("interval", [300, 86400])
def test_interval_edit_preserves_initial_ineligible_and_failure_schedules(
    monkeypatch, fields, interval
):
    record = source(**fields)
    previous = record.next_fetch_at
    update(monkeypatch, record, {"poll_interval_seconds": interval, "enabled": record.enabled})
    assert record.next_fetch_at == previous


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_jobs_keep_schedule_and_availability(monkeypatch, status):
    record = source()
    previous = record.next_fetch_at
    active = SimpleNamespace(status=status, available_at=NOW + timedelta(hours=6))
    update(monkeypatch, record, {"poll_interval_seconds": 300}, active)
    assert record.next_fetch_at == previous
    assert active.available_at == NOW + timedelta(hours=6)


@pytest.mark.parametrize("failures", [0, 1])
def test_reenable_only_makes_healthy_sources_immediately_due(monkeypatch, failures):
    record = source(enabled=False, consecutive_failures=failures)
    previous = record.next_fetch_at
    update(monkeypatch, record, {"enabled": True})
    assert record.next_fetch_at == (previous if failures else NOW)


@pytest.mark.parametrize("last_success", [SUCCESS, None])
def test_combined_enable_and_interval_edit_uses_cadence_rules(monkeypatch, last_success):
    record = source(enabled=False, last_success_at=last_success)
    previous = record.next_fetch_at
    update(monkeypatch, record, {"enabled": True, "poll_interval_seconds": 7200})
    assert record.next_fetch_at == (NOW + timedelta(hours=1) if last_success else previous)
