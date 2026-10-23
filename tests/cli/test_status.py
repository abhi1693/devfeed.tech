"""Status combines independent dependency projections without hiding outages."""

from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_cli import status
from devfeed_core.worker_queues import AI_QUEUES, QUEUES
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from sqlalchemy.exc import SQLAlchemyError

from tests.api.test_feed_snapshot_contracts_unit import NOW


@pytest.fixture
def dependencies(monkeypatch):
    # Results are already-grouped repository projections. No SQL conditions,
    # Redis pipelines, or scheduler liveness implementation are simulated.
    session = Mock()
    session.scalar.side_effect = [8, 12, 4, NOW - timedelta(hours=3), NOW - timedelta(hours=1)]
    session.execute.side_effect = [
        [("approved", 7), ("pending", 1)],
        [("queued", 3), ("succeeded", 5)],
        [("published", 10), ("unpublished", 2)],
        [("approved", 10), ("pending", 2)],
        [("queued", 2), ("succeeded", 10)],
        [("queued", 2), ("succeeded", 10)],
        [("queued", 1), ("failed", 1), ("succeeded", 20)],
        [("queued", 3), ("succeeded", 5)],
    ]
    monkeypatch.setattr(status, "session_factory", lambda: lambda: nullcontext(session))
    connection = Redis(host="redis.invalid", db=15)
    heartbeat = Mock(return_value=NOW.isoformat().encode())
    pipeline = MagicMock()
    pipeline.__enter__.return_value = pipeline
    depths = {name: index + 1 for index, name in enumerate(QUEUES)}
    pipeline.execute.return_value = list(depths.values())
    monkeypatch.setattr(connection, "get", heartbeat)
    monkeypatch.setattr(connection, "pipeline", Mock(return_value=pipeline))
    close = Mock()
    monkeypatch.setattr(connection, "close", close)
    queue = Queue("ingestion", connection=connection)
    monkeypatch.setattr(status, "get_queue", lambda: queue)
    workers = []
    for name, queues in [
        ("ai", ["article-analysis"]),
        ("background", ["ingestion"]),
        ("other", ["unrelated"]),
    ]:
        worker = Worker(queues, connection=connection, prepare_for_work=False, name=name)
        worker.get_state = Mock(return_value="idle")
        worker.pid, worker.last_heartbeat = 42, NOW
        workers.append(worker)
    monkeypatch.setattr(status.Worker, "all", Mock(return_value=workers))
    monkeypatch.setattr(status, "utcnow", lambda: NOW)
    return session, heartbeat, close, depths


def test_status_preserves_each_job_lane_and_limits_worker_lists_to_backend_queues(dependencies):
    _, _, close, depths = dependencies
    result = status.snapshot()
    assert (
        result["dependencies_ready"] and result["database_available"] and result["redis_available"]
    )
    assert (result["sources"], result["articles"], result["topics"]) == (8, 12, 4)
    assert result["source_review"] == {"approved": 7, "pending": 1}
    assert result["article_publication"] == {"published": 10, "unpublished": 2}
    assert result["jobs"]["failed"] == 1 and result["image_jobs"]["queued"] == 3
    assert result["oldest_active_image_job_at"] == NOW - timedelta(hours=3)
    assert result["queue_depths"] == depths and result["queue_depth"] == depths["ingestion"]
    assert result["analysis_queue_depth"] == sum(depths[name] for name in AI_QUEUES)
    assert [worker["name"] for worker in result["workers"]] == ["ai", "background"]
    assert [worker["name"] for worker in result["analysis_workers"]] == ["ai"]
    assert result["scheduler_healthy"]
    close.assert_called_once_with()


@pytest.mark.parametrize(
    "heartbeat,healthy",
    [
        (NOW.isoformat().encode(), True),
        ((NOW - timedelta(seconds=119)).isoformat().encode(), True),
        ((NOW - timedelta(seconds=120)).isoformat().encode(), False),
        ((NOW + timedelta(seconds=1)).isoformat().encode(), False),
        (None, False),
        (b"not-a-date", False),
        (b"\xff", False),
        (NOW.replace(tzinfo=None).isoformat().encode(), False),
    ],
)
def test_scheduler_health_requires_a_recent_nonfuture_timezone_aware_heartbeat(
    dependencies, heartbeat, healthy
):
    _, reader, close, _ = dependencies
    reader.return_value = heartbeat
    result = status.snapshot()
    assert result["scheduler_healthy"] is healthy
    assert result["dependencies_ready"]
    close.assert_called_once_with()


def test_database_outage_keeps_redis_diagnostics_and_actionable_safe_error(dependencies, caplog):
    session, _, close, _ = dependencies
    session.scalar.side_effect = SQLAlchemyError("private-database-credentials")
    result = status.snapshot()
    assert not result["dependencies_ready"] and not result["database_available"]
    assert result["redis_available"] and result["scheduler_healthy"]
    assert "devfeed db upgrade" in result["database_error"]
    assert "private-database-credentials" not in str(result) + caplog.text
    close.assert_called_once_with()


def test_redis_outage_keeps_database_counts_and_marks_unknown_depths_instead_of_zero(
    dependencies, caplog
):
    _, reader, close, _ = dependencies
    reader.side_effect = RedisError("redis://private-secret@internal")
    result = status.snapshot()
    assert result["database_available"] and result["sources"] == 8
    assert not result["dependencies_ready"] and not result["redis_available"]
    assert result["queue_depth"] is result["analysis_queue_depth"] is None
    assert result["queue_depths"] == dict.fromkeys(QUEUES)
    assert result["workers"] == result["analysis_workers"] == []
    assert not result["scheduler_healthy"] and result["scheduler_last_seen"] is None
    assert "private-secret" not in str(result) + caplog.text
    close.assert_called_once_with()
