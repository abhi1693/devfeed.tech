"""Paired PostgreSQL work and lock lifetimes at fixed notification throughput."""

import hashlib
import json
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from devfeed_aggregator.queue import get_queue
from devfeed_core.config import get_settings
from devfeed_core.job_dispatch import DispatchLane, JobDispatcher, dispatch_jobs
from devfeed_core.models import NotificationDelivery, utcnow
from devfeed_notifications import delivery
from devfeed_notifications.config import Settings
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.job import JobStatus
from sqlalchemy import select, text

from tests.support.postgres_work import postgres_work

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "partial",
        "http_outage",
        "soft_timeout",
        "slow_outage",
        "redis_repair",
        "rollback",
        "rollback_regroup",
        "redis_outage",
    ],
)
def test_notification_postgres_work_at_fixed_throughput(database, monkeypatch, scenario):
    count = int(os.environ.get("DEVFEED_PG_WORK_ITEMS", "20"))
    assert 20 <= count <= 1000 and count % 10 == 0
    engine = database.kw["bind"]
    settings = Settings(
        _env_file=None,
        notifications_enabled=True,
        chimely_api_url="http://fixture.invalid",
        chimely_admin_environment="admin",
        chimely_admin_api_key="fixture-admin-key",
    )
    monkeypatch.setattr(delivery, "get_settings", lambda: settings)
    monkeypatch.setattr(delivery, "session_factory", lambda: database)
    reports = []
    client_type = httpx.Client
    for size in (1, 10):
        now = utcnow()
        monkeypatch.setattr(get_settings(), "notification_delivery_batch_size", size)
        with database.begin() as session:
            session.execute(text("TRUNCATE notification_deliveries"))
            session.add_all(
                [
                    NotificationDelivery(
                        id=uuid.uuid5(uuid.NAMESPACE_URL, f"pg-work:{i}"),
                        event_key=f"pg-work:{i}",
                        dedup_key=hashlib.sha256(f"pg-work:{i}".encode()).hexdigest(),
                        audience="admin",
                        category="release.changed",
                        payload={},
                        available_at=now,
                        created_at=now,
                        status="queued",
                        attempts=0,
                    )
                    for i in range(count)
                ]
            )
        queue = get_queue("notifications")
        queue.connection.flushdb()
        clock, requests = SimpleNamespace(value=0, now=now), []
        monkeypatch.setattr(
            delivery, "time", SimpleNamespace(monotonic=lambda clock=clock: clock.value)
        )
        monkeypatch.setattr(delivery, "utcnow", lambda clock=clock: clock.now)

        def response(request, requests=requests, clock=clock):
            assert measured["active_connections"] == 0
            requests.append(json.loads(request.content)["idempotency_key"])
            if scenario in {"soft_timeout", "slow_outage"}:
                clock.value += 11
            if scenario in {"http_outage", "slow_outage"}:
                raise httpx.ConnectError("fixture destination unavailable")
            return httpx.Response(429 if scenario == "partial" and len(requests) % 5 == 0 else 201)

        monkeypatch.setattr(
            delivery.httpx,
            "Client",
            lambda **options: client_type(**options, transport=httpx.MockTransport(response)),
        )
        try:
            with postgres_work(engine) as measured:
                if scenario in {"rollback", "rollback_regroup"}:
                    with (
                        pytest.raises(RuntimeError, match="fixture rollback"),
                        database.begin() as session,
                    ):
                        dispatcher = JobDispatcher(database, queue, now)
                        statement = (
                            dispatcher._statement(DispatchLane("notifications"))
                            if scenario == "rollback"
                            else select(NotificationDelivery)
                        )
                        rows = list(session.scalars(statement.with_for_update()))
                        dispatcher._publish(
                            [("notifications", row, False, False) for row in rows],
                        )
                        raise RuntimeError("fixture rollback")
                if scenario == "redis_outage":

                    class Unavailable:
                        def enqueue(self, *args, **kwargs):
                            raise RedisConnectionError("fixture broker unavailable")

                    with pytest.raises(RedisConnectionError):
                        dispatch_jobs(database, Unavailable(), count, now, kind="notifications")
                assert dispatch_jobs(database, queue, count, now, kind="notifications") == count
                if scenario == "redis_repair":
                    queue.connection.delete(queue.key)
                    now += timedelta(seconds=301)
                    assert dispatch_jobs(database, queue, count, now, kind="notifications") == count
                for _ in range(count + 1):
                    for identity in list(queue.get_job_ids()):
                        assert queue.connection.lmove(
                            queue.key, f"{queue.key}:intermediate", "LEFT", "RIGHT"
                        )
                        job = queue.fetch_job(identity)
                        job.set_status(JobStatus.STARTED)
                        delivery.deliver_notification(job.args[0])
                        queue.connection.lrem(f"{queue.key}:intermediate", 0, identity)
                        job.delete()
                    if len(requests) == count:
                        break
                    assert dispatch_jobs(database, queue, count, now, kind="notifications") > 0
                assert len(requests) == len(set(requests)) == count
            measured["batch_size"] = size
            reports.append(dict(measured))
            assert measured["active_connections"] == 0 and measured["peak_connections"] == 1
        finally:
            queue.connection.close()
    # The soft-stop path is explicitly measured rather than silently equating
    # fewer Python work horses with less PostgreSQL work.
    if scenario not in {"soft_timeout", "slow_outage", "rollback_regroup"}:
        assert reports[1]["sql_calls"] <= reports[0]["sql_calls"]
        assert reports[1]["transactions"] <= reports[0]["transactions"]
    print(
        "NOTIFICATION_PG_WORK="
        + json.dumps(
            {
                "scenario": scenario,
                "deliveries": count,
                "accepted_batch_size": 1,
                "experimental_sql_regression": reports[1]["sql_calls"] > reports[0]["sql_calls"],
                "runs": reports,
            },
            sort_keys=True,
        )
    )
