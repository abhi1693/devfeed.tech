"""Persist admin/CLI cadence edits and exercise the real scheduler on disposable services."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler
from devfeed_cli.main import run
from devfeed_core import jobs
from devfeed_core.models import IngestionJob, Source
from sqlalchemy import func, select
from test_source_schedule import NOW, SUCCESS, source

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def schedule_clock(monkeypatch):
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)
    monkeypatch.setattr(scheduler, "utcnow", lambda: NOW)


def seed(database, **fields):
    record = source(**fields)
    with database.begin() as session:
        session.add(record)
        session.flush()
        return record.id


def edit(channel, admin_client, capsys, identifier, interval, *, enable=False):
    if channel == "admin":
        body = {"poll_interval_seconds": interval}
        if enable:
            body["enabled"] = True
        response = admin_client.patch(f"/v1/admin/sources/{identifier}", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["poll_interval_seconds"] == interval
    else:
        args = ["sources", "update", str(identifier), "--poll-interval", str(interval)]
        if enable:
            args.append("--enable")
        assert run(args) == 0, capsys.readouterr().err
        capsys.readouterr()


@pytest.mark.parametrize("channel", ["admin", "cli"])
@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (43200, 300, NOW),
        (300, 43200, SUCCESS + timedelta(hours=12)),
        (43200, 7200, NOW + timedelta(hours=1)),
    ],
)
def test_persisted_interval_changes_control_scheduler_eligibility(
    database, admin_client, capsys, channel, old, new, expected
):
    identifier = seed(
        database, poll_interval_seconds=old, next_fetch_at=SUCCESS + timedelta(seconds=old)
    )
    edit(channel, admin_client, capsys, identifier, new)
    with database() as session:
        record = session.get(Source, identifier)
        assert record.poll_interval_seconds == new
        assert record.next_fetch_at == expected
    assert scheduler.tick()["scheduled"] == int(expected <= NOW)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == int(
            expected <= NOW
        )


@pytest.mark.parametrize("channel", ["admin", "cli"])
@pytest.mark.parametrize("status", ["queued", "running"])
def test_interval_edit_coalesces_active_jobs_and_keeps_retry_delay(
    database, admin_client, capsys, channel, status
):
    identifier = seed(database)
    available = NOW + timedelta(hours=6)
    with database.begin() as session:
        job = IngestionJob(
            source_id=identifier,
            status=status,
            available_at=available,
            attempts=1,
            lease_token=uuid.uuid4() if status == "running" else None,
            lease_until=NOW + timedelta(minutes=10) if status == "running" else None,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    edit(channel, admin_client, capsys, identifier, 300, enable=True)
    assert scheduler.tick()["scheduled"] == 0
    with database() as session:
        assert session.get(Source, identifier).next_fetch_at == SUCCESS + timedelta(hours=12)
        job = session.get(IngestionJob, job_id)
        assert job.status == status and job.available_at == available
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1


@pytest.mark.parametrize("channel", ["admin", "cli"])
@pytest.mark.parametrize("retry_after", [0, 3 * 86400])
def test_terminal_failure_retry_after_survives_interval_edit(
    database, admin_client, capsys, channel, retry_after
):
    identifier = seed(database)
    expected = NOW + timedelta(seconds=max(86400, retry_after))
    with database.begin() as session:
        job = IngestionJob(source_id=identifier, status="running", attempts=1)
        session.add(job)
        session.flush()
        jobs.fail_job(session, job, "HTTP 429", retryable=False, retry_after=retry_after)
        assert session.get(Source, identifier).next_fetch_at == expected
    edit(channel, admin_client, capsys, identifier, 300, enable=True)
    with database() as session:
        assert session.get(Source, identifier).next_fetch_at == expected
    assert scheduler.tick()["scheduled"] == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"last_success_at": None},
        {"enabled": False},
        {"approval_status": "pending"},
        {"approval_status": "rejected"},
    ],
)
def test_initial_and_ineligible_schedules_are_preserved(database, admin_client, capsys, fields):
    identifier = seed(database, **fields)
    edit("admin", admin_client, capsys, identifier, 300)
    with database() as session:
        assert session.get(Source, identifier).next_fetch_at == SUCCESS + timedelta(hours=12)
    assert scheduler.tick()["scheduled"] == 0


def test_unchanged_interval_and_unrelated_admin_patch_preserve_due_time(
    database, admin_client, capsys
):
    identifier = seed(database)
    edit("admin", admin_client, capsys, identifier, 43200, enable=True)
    response = admin_client.patch(f"/v1/admin/sources/{identifier}", json={"name": "Renamed"})
    assert response.status_code == 200
    with database() as session:
        assert session.get(Source, identifier).next_fetch_at == SUCCESS + timedelta(hours=12)


def test_source_without_success_and_overdue_initial_time_remains_eligible(
    database, admin_client, capsys
):
    initial = NOW - timedelta(hours=1)
    identifier = seed(database, last_success_at=None, next_fetch_at=initial)
    edit("admin", admin_client, capsys, identifier, 300)
    with database() as session:
        assert session.get(Source, identifier).next_fetch_at == initial
    assert scheduler.tick()["scheduled"] == 1
