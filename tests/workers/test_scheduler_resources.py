"""Bounded scheduler pools preserve dispatch, recovery and concurrent feed progress."""

import json
import platform
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler
from devfeed_aggregator.scheduler_resources import SchedulerResources
from devfeed_core.config import get_settings
from devfeed_core.models import IngestionJob, Source, utcnow
from sqlalchemy import event, select, text

from tests.support import scheduler as fixtures

pytestmark = pytest.mark.integration


def test_reused_pool_reconnects_after_its_idle_backend_is_lost(database):
    with SchedulerResources() as resources:
        with resources.factory() as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
        with database() as observer:
            assert observer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with resources.factory() as session:
            assert session.scalar(text("SELECT 1")) == 1
            assert session.scalar(text("SELECT pg_backend_pid()")) != pid


def test_pool_serves_main_partner_and_reader_lanes_without_overflow(database):
    settings = get_settings().model_copy(update={"scheduler_database_pool_size": 3})
    barrier = threading.Barrier(3)
    with SchedulerResources(settings) as resources:

        def operation():
            with resources.factory() as session:
                backend = session.scalar(text("SELECT pg_backend_pid()"))
                barrier.wait(timeout=5)
                return backend

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(operation) for _ in range(3)]
            pids = [future.result(timeout=10) for future in futures]
        assert len(set(pids)) == 3
        assert resources.engine.pool.checkedout() == 0
        assert resources.engine.pool.overflow() == 0


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


def test_scheduler_reuse_keeps_query_work_and_reduces_connection_churn(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "ai_quota_pacing_enabled", False)
    settings = get_settings().model_copy(update={"database_pool_enabled": False})
    article = fixtures.retained_analysis_jobs(database)
    reports = {}
    for reuse in (False, True):
        settings.scheduler_connection_reuse_enabled = reuse
        with SchedulerResources(settings) as resources:
            scheduler.tick(resources)
            counts = {"connections": 0, "statements": 0}

            def connected(*_, current=counts):
                current["connections"] += 1

            def statement(*_, current=counts):
                current["statements"] += 1

            event.listen(resources.engine, "connect", connected)
            event.listen(resources.engine, "before_cursor_execute", statement)
            cpu, durations, outcomes = time.process_time(), [], []
            try:
                for _ in range(10):
                    started = time.perf_counter()
                    outcomes.append(scheduler.tick(resources))
                    durations.append(time.perf_counter() - started)
            finally:
                event.remove(resources.engine, "connect", connected)
                event.remove(resources.engine, "before_cursor_execute", statement)
            reports[str(reuse)] = {
                **counts,
                "cpu_seconds": time.process_time() - cpu,
                "median_tick_seconds": statistics.median(durations),
                "outcomes": outcomes,
            }
    old, new = reports["False"], reports["True"]
    assert new["outcomes"] == old["outcomes"]
    assert new["statements"] == old["statements"]
    assert new["connections"] == 0 and old["connections"] > 100
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
