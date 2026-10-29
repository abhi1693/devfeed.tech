"""Worker telemetry composes safe transport and durable task facts without writes."""

import json
import uuid
import zlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import dependencies, workers
from devfeed_core.models import SourceCandidate, SourceDiscoveryJob
from fastapi import HTTPException
from redis.exceptions import ConnectionError

from tests.admin.sources.test_source_pipeline_contracts_unit import candidate
from tests.support.admin_editorial import operator_api
from tests.support.source_readiness import NOW, job

HANDLER = "devfeed_aggregator.discovery_tasks.process_candidate"


@pytest.mark.parametrize("args", [[], ["not-a-uuid"], [str(uuid.uuid4()), str(uuid.uuid4())], [42]])
def test_transport_identity_rejects_missing_ambiguous_or_non_uuid_arguments(args):
    raw = json.dumps([HANDLER, None, args, {}]).encode()
    assert workers.job_identity(zlib.compress(raw)) is None


@pytest.mark.parametrize("retained", [False, True])
def test_current_discovery_work_uses_candidate_name_without_disclosing_result_payloads(retained):
    proposed = candidate(status="pending")
    current = job(
        SourceDiscoveryJob,
        status="running",
        candidate_id=proposed.id,
        stage="discover",
        result={"private": "upstream source evidence"},
    )
    pipe, connection, session = Mock(), Mock(), Mock()
    connection.pipeline.return_value.__enter__ = Mock(return_value=pipe)
    connection.pipeline.return_value.__exit__ = Mock(return_value=False)
    raw = zlib.compress(json.dumps([HANDLER, None, [str(current.id)], {}]).encode())
    pipe.execute.return_value = [(raw, (NOW - timedelta(seconds=12)).isoformat().encode())]
    rows = {SourceDiscoveryJob: [current], SourceCandidate: [proposed] if retained else []}
    session.scalars.side_effect = lambda statement: rows.get(
        statement.column_descriptions[0]["entity"], []
    )
    result = workers.current_jobs(connection, session, ["rq-discovery", "rq-discovery"], NOW)
    assert list(result) == ["rq-discovery"]
    output = result["rq-discovery"]
    assert (output.id, output.kind, output.status, output.elapsed_seconds) == (
        current.id,
        "source-discovery",
        "running",
        12,
    )
    assert output.target_name == (proposed.name if retained else None)
    assert output.source_id is None and "upstream source evidence" not in output.model_dump_json()
    pipe.hmget.assert_called_once_with("rq:job:rq-discovery", "data", "started_at")
    session.add.assert_not_called()


def test_oversized_worker_registry_fails_before_fetching_unbounded_worker_or_job_hashes():
    connection, session = Mock(), Mock()
    connection.smembers.return_value = {f"rq:worker:{index}".encode() for index in range(1001)}
    with pytest.raises(HTTPException) as failure:
        workers.read_workers(connection, session, NOW)
    assert failure.value.status_code == 503 and "snapshot limit" in failure.value.detail
    connection.pipeline.assert_not_called()
    session.scalars.assert_not_called()


def test_queue_counts_merge_shared_physical_lanes_and_keep_transport_depth_distinct():
    connection, pipe, session = Mock(), Mock(), Mock()
    connection.pipeline.return_value.__enter__ = Mock(return_value=pipe)
    connection.pipeline.return_value.__exit__ = Mock(return_value=False)
    pipe.execute.return_value = [
        7 if name == "source-analysis" else 0 for name in workers.physical_queues()
    ]
    # Source-analysis combines profile relevance and background discovery
    # assessments; review-required verification aggregates have two lane groups.
    session.execute.return_value = [
        ("source-analysis", "queued", 2, NOW - timedelta(minutes=5)),
        ("source-analysis", "queued", 3, NOW - timedelta(minutes=10)),
        ("source-analysis", "running", 1, NOW - timedelta(minutes=2)),
        ("source-analysis", "failed", 4, NOW - timedelta(days=1)),
        ("research-verification", "review_required", 2, NOW - timedelta(days=1)),
        ("research-verification", "review_required", 1, NOW - timedelta(hours=1)),
    ]
    registered = [
        SimpleNamespace(registered=True, queues=["source-analysis"], state=state)
        for state in ("busy", "idle", "suspended")
    ]
    registered.append(SimpleNamespace(registered=False, queues=["source-analysis"], state="busy"))
    result = {item.name: item for item in workers.queue_snapshot(connection, session, registered)}
    lane = result["source-analysis"]
    assert (lane.dispatched, lane.queued, lane.running, lane.failed) == (7, 5, 1, 4)
    assert lane.oldest_queued_at == NOW - timedelta(minutes=10)
    assert (
        lane.registered_workers,
        lane.busy_workers,
        lane.idle_workers,
        lane.suspended_workers,
    ) == (3, 1, 1, 1)
    assert result["research-verification"].review_required == 3
    assert result["ingestion"].queued == 0 and result["ingestion"].oldest_queued_at is None
    session.add.assert_not_called()


@pytest.mark.parametrize("detail", [False, True])
def test_worker_redis_failure_returns_safe_retryable_http_status_without_transport_diagnostics(
    monkeypatch, detail
):
    connection = Mock()
    connection.pipeline.side_effect = ConnectionError("redis://operator:private@unavailable/15")
    connection.smembers.side_effect = ConnectionError("redis://operator:private@unavailable/15")
    with operator_api(workers) as api:
        api.app.dependency_overrides[dependencies.get_redis] = lambda: connection
        response = api.client.get(
            "/v1/admin/workers/background-1" if detail else "/v1/admin/workers"
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Worker telemetry is temporarily unavailable"
        assert "private" not in response.text and "redis://" not in response.text
        api.session.commit.assert_not_called()
