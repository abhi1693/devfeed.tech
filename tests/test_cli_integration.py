import io
import json
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from devfeed_aggregator.queue import get_queue
from devfeed_cli import status
from devfeed_cli.main import run
from devfeed_core import services
from devfeed_core.jobs import claim_job
from devfeed_core.models import Article, IngestionJob, utcnow
from devfeed_core.schemas import SourceCreate
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Worker
from rq.serializers import JSONSerializer
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def invoke(capsys, *args, code=0):
    result = run(list(args))
    output = capsys.readouterr()
    assert result == code, output.err
    if code:
        assert output.out == ""
        return output.err
    return json.loads(output.out) if output.out else None


def test_submit_reuses_source_and_active_job_without_overwriting_settings(database, capsys):
    first = invoke(
        capsys,
        "sources",
        "add",
        "https://EXAMPLE.com:443/rss#feed",
        "--type",
        "publisher",
        "--name",
        "Original",
    )
    second = invoke(
        capsys,
        "sources",
        "add",
        "https://example.com/rss",
        "--type",
        "publisher",
        "--name",
        "Replacement",
        "--poll-interval",
        "600",
    )
    assert first["created"] is True and second["created"] is False
    assert second["source"]["name"] == "Original"
    assert second["source"]["poll_interval_seconds"] == 1800
    assert first["source"]["id"] == second["source"]["id"]
    assert first["job"]["id"] == second["job"]["id"]
    assert invoke(capsys, "sources", "fetch", first["source"]["id"])["id"] == first["job"]["id"]
    assert (
        invoke(capsys, "sources", "show", first["source"]["id"])["feed_url"]
        == "https://example.com/rss"
    )
    assert len(invoke(capsys, "sources", "list")) == 1


def test_bulk_import_is_atomic_and_deduplicates_urls(database, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("sys.stdin", io.StringIO("https://example.com/rss\nhttp://127.0.0.1/rss\n"))
    invoke(capsys, "sources", "import", "-", "--type", "publisher", code=2)
    assert invoke(capsys, "sources", "list") == []
    feeds = tmp_path / "feeds.txt"
    feeds.write_text(
        "# Source list\nhttps://example.com/rss\n\nhttps://other.example/rss\nhttps://EXAMPLE.com:443/rss#duplicate\n",
        encoding="utf-8",
    )
    result = invoke(capsys, "sources", "import", str(feeds), "--type", "publisher")
    assert result["submitted"] == result["created"] == 2
    assert all(item["job"]["status"] == "queued" for item in result["items"])
    again = invoke(capsys, "sources", "import", str(feeds), "--type", "publisher")
    assert again["created"] == 0
    assert [item["job"]["id"] for item in again["items"]] == [
        item["job"]["id"] for item in result["items"]
    ]


def test_concurrent_submission_creates_one_source_and_job(database):
    def submit(_):
        validated = services.validate_source(
            SourceCreate(
                name="Example", feed_url="https://example.com/rss", source_type="publisher"
            )
        )
        with database.begin() as session:
            source, created, job = services.submit_source(session, validated)
            return source.id, created, job.id

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert len({row[0] for row in results}) == 1
    assert sum(row[1] for row in results) == 1
    assert len({row[2] for row in results}) == 1


def test_resubmission_with_conflicting_type_is_rejected(database, capsys):
    first = invoke(capsys, "sources", "add", "https://example.com/rss", "--type", "aggregator")
    error = invoke(
        capsys, "sources", "add", "https://example.com/rss", "--type", "publisher", code=2
    )
    assert "different source type" in error
    assert invoke(capsys, "sources", "show", first["source"]["id"])["source_type"] == "aggregator"
    assert invoke(capsys, "sources", "list", "--type", "publisher") == []
    assert len(invoke(capsys, "sources", "list", "--type", "aggregator")) == 1


def test_disabled_submission_and_source_updates(database, capsys):
    result = invoke(
        capsys, "sources", "add", "https://example.com/rss", "--type", "publisher", "--disabled"
    )
    source_id = result["source"]["id"]
    assert result["job"] is None
    assert result["source"]["name"] == "Engineering Example"
    assert invoke(capsys, "sources", "list", "--enabled") == []
    assert invoke(capsys, "sources", "list", "--disabled")[0]["id"] == source_id
    assert "Enable the source" in invoke(capsys, "sources", "fetch", source_id, code=2)
    assert (
        invoke(capsys, "sources", "add", "https://example.com/rss", "--type", "publisher")["job"]
        is None
    )
    changed = invoke(
        capsys,
        "sources",
        "update",
        source_id,
        "--enable",
        "--name",
        "Renamed",
        "--poll-interval",
        "600",
    )
    assert changed["enabled"] is True and changed["name"] == "Renamed"
    assert changed["poll_interval_seconds"] == 600
    assert invoke(capsys, "sources", "fetch", source_id)["status"] == "queued"
    assert invoke(capsys, "sources", "update", source_id, "--disable")["enabled"] is False
    invoke(capsys, "sources", "show", str(uuid.uuid4()), code=2)


def test_cli_taxonomy_shares_tree_validation_with_api(client, capsys):
    root = invoke(capsys, "categories", "add", "--name", "Engineering", "--slug", "engineering")
    leaf = invoke(
        capsys,
        "categories",
        "add",
        "--name",
        "Runtime",
        "--slug",
        "runtime",
        "--parent-id",
        root["id"],
        "--keyword",
        "runtime",
    )
    tag = invoke(
        capsys,
        "tags",
        "add",
        "--name",
        "Custom",
        "--slug",
        "custom",
        "--category-id",
        leaf["id"],
        "--alias",
        "custom-engine",
    )
    assert client.get("/v1/categories/tree").json()[0]["children"][0]["id"] == leaf["id"]
    assert invoke(capsys, "tags", "list")[0]["aliases"] == ["custom-engine"]
    assert "ancestor" in invoke(
        capsys, "categories", "update", root["id"], "--parent-id", leaf["id"], code=2
    )
    assert "not found" in invoke(
        capsys, "categories", "update", leaf["id"], "--parent-id", str(uuid.uuid4()), code=2
    )
    moved = invoke(capsys, "categories", "update", leaf["id"], "--root", "--clear-keywords")
    assert moved["parent_id"] is None and moved["keywords"] == []
    changed = invoke(
        capsys, "tags", "update", tag["id"], "--slug", "renamed", "--clear-aliases", "--ungroup"
    )
    assert changed["slug"] == "renamed" and changed["aliases"] == []
    assert changed["category_id"] is None
    assert len(invoke(capsys, "categories", "list")) == 2
    invoke(capsys, "categories", "add", "--name", "Duplicate", "--slug", "engineering", code=2)
    invoke(capsys, "tags", "update", tag["id"], "--category-id", str(uuid.uuid4()), code=2)


def test_job_retry_preserves_history_and_coalesces_new_run(database, capsys):
    first = invoke(capsys, "sources", "add", "https://example.com/rss", "--type", "publisher")
    job_id = first["job"]["id"]
    invoke(capsys, "jobs", "retry", job_id, code=2)
    with database.begin() as session:
        job = session.get(IngestionJob, uuid.UUID(job_id))
        job.status = "failed"
        job.finished_at = utcnow()
        job.error = "Original failure"
    retry = invoke(capsys, "jobs", "retry", job_id)
    assert retry["id"] != job_id and retry["status"] == "queued"
    assert invoke(capsys, "jobs", "retry", job_id)["id"] == retry["id"]
    assert invoke(capsys, "jobs", "show", job_id)["error"] == "Original failure"
    assert len(invoke(capsys, "jobs", "list", "--source-id", first["source"]["id"])) == 2
    assert len(invoke(capsys, "jobs", "list", "--status", "failed")) == 1
    invoke(capsys, "jobs", "show", str(uuid.uuid4()), code=2)
    invoke(capsys, "sources", "update", first["source"]["id"], "--disable")
    invoke(capsys, "jobs", "retry", job_id, code=2)


@pytest.mark.parametrize("by_source", [False, True])
def test_manual_dispatch_bypasses_delays_without_dispatching_other_jobs(
    database, capsys, by_source
):
    first = invoke(capsys, "sources", "add", "https://example.com/rss", "--type", "publisher")
    other = invoke(capsys, "sources", "add", "https://other.example/rss", "--type", "publisher")
    job_id = uuid.UUID(first["job"]["id"])
    with database.begin() as session:
        job = session.get(IngestionJob, job_id)
        job.available_at = utcnow() + timedelta(minutes=10)
        job.dispatched_at = utcnow()
        job.attempts = 2
        job.error = "Previous failure"
    if by_source:
        result = invoke(capsys, "sources", "fetch", first["source"]["id"], "--force")
    else:
        result = invoke(capsys, "jobs", "dispatch", str(job_id))
    assert result["id"] == str(job_id)
    assert result["status"] == "queued" and result["attempts"] == 2
    assert result["error"] == "Previous failure" and result["dispatched_at"] is not None
    queue = get_queue()
    try:
        assert queue.count == 1
        assert tuple(queue.get_jobs()[0].args) == (str(job_id),)
        assert queue.connection.get("devfeed:scheduler:heartbeat") is None
    finally:
        queue.connection.close()
    with database() as session:
        assert session.get(IngestionJob, job_id).available_at <= utcnow()
        assert session.get(IngestionJob, uuid.UUID(other["job"]["id"])).dispatched_at is None
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 2


def test_manual_dispatch_does_not_steal_running_lease(database, capsys):
    first = invoke(capsys, "sources", "add", "https://example.com/rss", "--type", "publisher")
    job_id = uuid.UUID(first["job"]["id"])
    with database.begin() as session:
        job, _ = claim_job(session, job_id)
        token, lease_until = job.lease_token, job.lease_until
    assert "Only queued jobs" in invoke(capsys, "jobs", "dispatch", str(job_id), code=2)
    assert "Only queued jobs" in invoke(
        capsys, "sources", "fetch", first["source"]["id"], "--force", code=2
    )
    with database() as session:
        job = session.get(IngestionJob, job_id)
        assert job.status == "running" and job.attempts == 1
        assert job.lease_token == token and job.lease_until == lease_until
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1
    queue = get_queue()
    try:
        assert queue.count == 0
    finally:
        queue.connection.close()


def test_cli_submission_scheduler_and_real_forking_worker(database, capsys):
    result = invoke(capsys, "sources", "add", "https://example.com/rss", "--type", "publisher")
    assert invoke(capsys, "scheduler", "--once")["dispatched"] == 1
    # Exercise the real forking worker in a fresh process, outside pytest's threads.
    # Replace only publisher I/O; persistence, Redis, dispatch and worker execution are real.
    worker_code = """
import sys
from pathlib import Path
from devfeed_aggregator import tasks
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_cli.main import run
rss = Path(sys.argv[1]).read_bytes()
tasks.fetch_feed = lambda *args: FetchResult(200, rss, args[0])
raise SystemExit(run(["worker", "--burst", "--name", "cli-test-worker"]))
"""
    process = subprocess.run(
        [sys.executable, "-c", worker_code, str(Path(__file__).parent / "fixtures/feed.xml")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    completed = invoke(capsys, "jobs", "show", result["job"]["id"])
    assert completed["status"] == "succeeded" and completed["articles_created"] == 2
    snapshot = invoke(capsys, "status")
    assert snapshot["dependencies_ready"] is True
    assert snapshot["scheduler_healthy"] is True
    assert snapshot["articles"] == 2 and snapshot["queue_depth"] == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 2


def test_status_reports_worker_registration_and_stale_scheduler(database, capsys):
    queue = get_queue()
    worker = Worker(
        [queue], connection=queue.connection, serializer=JSONSerializer, name="visible-worker"
    )
    worker.register_birth()
    try:
        queue.connection.set(
            "devfeed:scheduler:heartbeat", (utcnow() - timedelta(minutes=5)).isoformat()
        )
        result = invoke(capsys, "status")
        assert result["workers"][0]["name"] == "visible-worker"
        assert result["workers"][0]["last_heartbeat"] is not None
        assert result["scheduler_healthy"] is False
    finally:
        worker.register_death()
        queue.connection.close()


def test_status_shows_broker_outage_but_submissions_remain_durable(database, capsys, monkeypatch):
    result = invoke(capsys, "sources", "add", "https://example.com/rss", "--type", "publisher")
    queue = get_queue()

    def unavailable(*args, **kwargs):
        raise RedisConnectionError("Unavailable")

    monkeypatch.setattr(queue.connection, "get", unavailable)
    monkeypatch.setattr(status, "get_queue", lambda: queue)
    assert run(["status"]) == 1
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["database_available"] is True and snapshot["redis_available"] is False
    assert snapshot["jobs"] == {"queued": 1}
    assert invoke(capsys, "jobs", "show", result["job"]["id"])["status"] == "queued"


def test_installed_cli_worker(database):
    # Execute the installed entrypoint in its own process, without an API server.
    result = subprocess.run(
        [sys.executable, "-m", "devfeed_cli", "worker", "--burst"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
