"""Redis reuse preserves the scheduler's PostgreSQL work and connection policy."""

import json
import platform
import statistics
import time
import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler
from devfeed_aggregator.scheduler_resources import SchedulerResources
from devfeed_core.config import get_settings
from devfeed_core.models import IngestionJob, Source, utcnow
from psycopg import Cursor
from sqlalchemy import event, select, text

from tests.support import scheduler as fixtures

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("reuse", [False, True])
def test_main_loop_keeps_queued_work_and_recovers_expired_leases(database, monkeypatch, reuse):
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    now = utcnow()
    with database.begin() as session:
        jobs = []
        for status in ("queued", "running"):
            url = f"https://scheduler.invalid/{uuid.uuid4()}.xml"
            source = Source(
                name=status,
                feed_url=url,
                source_type="publisher",
                enabled=True,
                approval_status="approved",
                next_fetch_at=now + timedelta(days=1),
            )
            session.add(source)
            session.flush()
            job = IngestionJob(
                source_id=source.id,
                status=status,
                attempts=int(status == "running"),
                available_at=now,
                lease_token=uuid.uuid4() if status == "running" else None,
                lease_until=now - timedelta(seconds=1) if status == "running" else None,
            )
            session.add(job)
            session.flush()
            jobs.append(job.id)
    settings = get_settings().model_copy(
        update={
            "database_pool_enabled": False,
            "scheduler_connection_reuse_enabled": reuse,
        }
    )
    with SchedulerResources(settings) as resources:
        first = scheduler.tick(resources)
        assert first["recovered"] == 1 and first["dispatched"] >= 1
        second = scheduler.tick(resources)
        assert second["recovered"] == 0
    with database() as session:
        rows = session.scalars(select(IngestionJob).where(IngestionJob.id.in_(jobs))).all()
        assert len(rows) == 2
        assert all(job.status == "queued" and job.lease_token is None for job in rows)
        assert session.get(IngestionJob, jobs[0]).dispatched_at is not None


def test_scheduler_reuse_keeps_database_work_and_reduces_redis_clients(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "ai_quota_pacing_enabled", False)
    settings = get_settings().model_copy(update={"database_pool_enabled": False})
    article = fixtures.retained_analysis_jobs(database)
    from unittest.mock import Mock

    from devfeed_aggregator import scheduler_resources

    lookup = Mock(wraps=scheduler_resources.get_queue)
    monkeypatch.setattr(scheduler_resources, "get_queue", lookup)
    reports = {}
    for reuse in (False, True):
        settings.scheduler_connection_reuse_enabled = reuse
        with SchedulerResources(settings) as resources:
            scheduler.tick(resources)
            lookup.reset_mock()
            engine = resources.factory.kw["bind"]
            counts = {"connections": 0, "statements": 0, "wire_commands": 0, "closed": 0}

            class ObservedCursor(Cursor):
                def execute(self, *args, _counts=counts, **kwargs):
                    _counts["wire_commands"] += 1
                    return super().execute(*args, **kwargs)

            def cursor_factory(_dialect, _record, _args, options):
                options["cursor_factory"] = ObservedCursor

            def closed(*_, current=counts):
                current["closed"] += 1

            def connected(*_, current=counts):
                current["connections"] += 1

            def statement(*_, current=counts):
                current["statements"] += 1

            event.listen(engine, "connect", connected)
            event.listen(engine, "before_cursor_execute", statement)
            event.listen(engine, "do_connect", cursor_factory)
            event.listen(engine, "close", closed)
            cpu, durations, outcomes = time.process_time(), [], []
            try:
                for _ in range(10):
                    started = time.perf_counter()
                    outcomes.append(scheduler.tick(resources))
                    durations.append(time.perf_counter() - started)
            finally:
                event.remove(engine, "connect", connected)
                event.remove(engine, "before_cursor_execute", statement)
                event.remove(engine, "do_connect", cursor_factory)
                event.remove(engine, "close", closed)
            reports[str(reuse)] = {
                **counts,
                "redis_clients": lookup.call_count,
                "cpu_seconds": time.process_time() - cpu,
                "median_tick_seconds": statistics.median(durations),
                "outcomes": outcomes,
            }
    old, new = reports["False"], reports["True"]
    assert new["outcomes"] == old["outcomes"]
    assert new["statements"] == old["statements"]
    assert new["wire_commands"] == old["wire_commands"]
    assert new["connections"] == old["connections"]
    assert new["closed"] == old["closed"]
    assert new["redis_clients"] == 0 and old["redis_clients"] > 0
    with database() as session:
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM article_analysis_jobs "
                    "WHERE article_id=:id AND status='succeeded'"
                ),
                {"id": article},
            )
            == 100_000
        )
    print(
        json.dumps(
            {
                "architecture": platform.machine(),
                "retained_jobs": 100_000,
                "ticks": 10,
                "results": reports,
            }
        )
    )


def test_owned_queue_reconnects_without_replacing_its_delivery_policy(database):
    with SchedulerResources() as resources:
        queue = resources.queue("ingestion")
        assert queue.connection.ping()
        queue.connection.connection_pool.disconnect()
        assert resources.queue("ingestion") is queue
        assert queue.connection.ping()


@pytest.mark.parametrize("reuse", [False, True])
def test_resources_use_the_existing_database_engine_without_opening_or_closing_it(database, reuse):
    engine = database.kw["bind"]
    calls = []

    def connected(*_):
        calls.append("connect")

    def disposed(*_):
        calls.append("dispose")

    event.listen(engine, "connect", connected)
    event.listen(engine, "engine_disposed", disposed)
    try:
        settings = get_settings().model_copy(update={"scheduler_connection_reuse_enabled": reuse})
        with SchedulerResources(settings) as resources:
            assert resources.factory.kw["bind"] is engine
        assert calls == []
    finally:
        event.remove(engine, "connect", connected)
        event.remove(engine, "engine_disposed", disposed)
