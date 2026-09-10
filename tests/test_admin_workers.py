"""Worker telemetry must stay read-only, authenticated, and separate from job success."""

import json
import uuid
import zlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api.dependencies import get_redis, get_session
from devfeed_admin_api.workers import job_identity
from redis.exceptions import ConnectionError
from test_admin_auth import complete
from test_admin_auth import oidc_app as oidc_app

NOW = datetime.now(UTC)


class TelemetryStore:
    def __init__(self):
        self.hashes = {}
        self.members = set()
        self.commands = []
        self.unavailable = False

    def smembers(self, key):
        if self.unavailable:
            raise ConnectionError("secret connection URL")
        assert key == "rq:workers"
        return self.members

    def hmget(self, key, *fields):
        return [self.hashes.get(key, {}).get(field) for field in fields]

    def ttl(self, key):
        return 90 if key in self.hashes else -2

    def llen(self, key):
        return 12 if key == "rq:queue:analysis" else 0

    def pipeline(self, **kwargs):
        store = self

        class Pipeline:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def hmget(self, *args):
                store.commands.append(("hmget", args))

            def ttl(self, *args):
                store.commands.append(("ttl", args))

            def llen(self, *args):
                store.commands.append(("llen", args))

            def execute(self):
                commands, store.commands = store.commands, []
                return [getattr(store, name)(*args) for name, args in commands]

        return Pipeline()

    def worker(self, name="ai-1", **fields):
        key = "rq:worker:" + name
        self.members.add(key.encode())
        self.hashes[key] = {
            "queues": b"analysis,relationships",
            "state": b"busy",
            "birth": (NOW - timedelta(hours=1)).isoformat().encode(),
            "last_heartbeat": NOW.isoformat().encode(),
            "successful_job_count": b"42",
            "failed_job_count": b"3",
            "hostname": b"host",
            "pid": b"10",
            **fields,
        }


@pytest.fixture
def telemetry(oidc_app):
    complete(oidc_app)
    store = TelemetryStore()
    session = SimpleNamespace(execute=lambda query: [], get=lambda *args: None)
    oidc_app.client.app.dependency_overrides[get_redis] = lambda: store
    oidc_app.client.app.dependency_overrides[get_session] = lambda: session
    return oidc_app.client, store, session


@pytest.mark.parametrize("path", ["/v1/admin/workers", "/v1/admin/workers/ai-1"])
def test_workers_require_auth_before_storage(oidc_app, path):
    oidc_app.client.app.dependency_overrides[get_redis] = lambda: pytest.fail("Redis before auth")
    oidc_app.client.app.dependency_overrides[get_session] = lambda: pytest.fail("DB before auth")
    assert oidc_app.client.get(path).status_code == 401


def test_read_only_snapshot_excludes_expired_and_unrelated_workers(telemetry):
    client, store, _ = telemetry
    store.worker()
    store.worker("foreign", queues=b"another-app")
    store.members.add(b"rq:worker:expired")
    response = client.get("/v1/admin/workers")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [w["name"] for w in body["workers"]] == ["ai-1"]
    worker = body["workers"][0]
    assert worker["completed_executions"] == 42 and worker["failed_executions"] == 3
    assert worker["role"] == "ai" and worker["registered"] is True
    assert body["queues"][1]["dispatched"] == 12
    assert body["queues"][1]["succeeded"] == 0
    assert b"rq:worker:expired" in store.members


def test_stopped_worker_is_offline_and_not_queue_capacity(telemetry):
    client, store, _ = telemetry
    store.worker(death=NOW.isoformat().encode())
    response = client.get("/v1/admin/workers")
    assert response.json()["workers"][0]["state"] == "offline"
    assert response.json()["queues"][1]["registered_workers"] == 0


def test_missing_worker_and_outage_are_not_empty_success(telemetry):
    client, store, _ = telemetry
    assert client.get("/v1/admin/workers/gone").status_code == 404
    store.unavailable = True
    response = client.get("/v1/admin/workers")
    assert response.status_code == 503 and "secret" not in response.text


def test_current_verification_links_to_durable_research_without_payloads(telemetry):
    from devfeed_core.models import ResearchVerificationJob, TopicAnalysisJob, TopicProposal

    client, store, session = telemetry
    identifier, proposal_id = uuid.uuid4(), uuid.uuid4()
    store.worker(current_job=b"rq-1")
    store.hashes["rq:job:rq-1"] = {
        "data": zlib.compress(
            json.dumps(
                [
                    "devfeed_aggregator.research_verification_tasks.verify_research",
                    None,
                    [str(identifier)],
                    {"private": "never return"},
                ]
            ).encode()
        ),
        "started_at": NOW.isoformat().encode(),
    }
    records = {
        ResearchVerificationJob: SimpleNamespace(status="running", outcome=None),
        TopicAnalysisJob: SimpleNamespace(proposal_id=proposal_id),
        TopicProposal: SimpleNamespace(proposed={"name": "React"}),
    }
    session.get = lambda model, identifier: records[model]
    response = client.get("/v1/admin/workers/ai-1")
    assert response.status_code == 200, response.text
    job = response.json()["current_job"]
    assert job["kind"] == "research-verification" and job["id"] == str(identifier)
    assert job["proposal_id"] == str(proposal_id) and job["target_name"] == "React"
    assert "never return" not in response.text


@pytest.mark.parametrize(
    "data",
    [
        b"not-json",
        b"\x80\x04pickle",
        b"{}",
        b"null",
        b"[[],null,[],{}]",
        zlib.compress(b"x" * 100000),
    ],
)
def test_invalid_or_oversized_job_payloads_are_not_deserialized(data):
    assert job_identity(data) is None


def test_job_finished_between_worker_and_job_read(telemetry):
    client, store, _ = telemetry
    store.worker(current_job=b"expired-job")
    response = client.get("/v1/admin/workers/ai-1")
    assert response.status_code == 200
    assert response.json()["current_job"]["id"] is None
