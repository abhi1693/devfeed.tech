from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import worker
from devfeed_core.config import get_settings


def test_dedicated_worker_publishes_its_name_without_following_links(tmp_path, monkeypatch):
    target = tmp_path / "protected"
    target.write_text("unchanged")
    marker = tmp_path / "worker-name"
    marker.symlink_to(target)
    monkeypatch.setattr(worker, "runtime_marker", lambda _: marker)
    monkeypatch.setattr(
        "sys.argv", ["worker", "--name", "worker-123", "--burst", "--max-jobs", "3"]
    )
    run = Mock()
    monkeypatch.setattr(worker, "run", run)
    assert worker.queue_worker_main("images", "Images worker") == 0
    assert marker.read_text() == "worker-123"
    assert target.read_text() == "unchanged"
    run.assert_called_once_with(burst=True, name="worker-123", max_jobs=3, queue_name="images")


def test_standalone_worker_selects_queue_and_records_unique_runtime_identity(tmp_path, monkeypatch):
    marker = tmp_path / "worker-name"
    monkeypatch.setattr(worker, "runtime_marker", lambda _: marker)
    monkeypatch.setattr(
        "sys.argv", ["devfeed-worker", "--queue", "article-analysis", "--burst", "--max-jobs", "2"]
    )
    run = Mock()
    monkeypatch.setattr(worker, "run", run)
    assert worker.main() == 0
    identity = marker.read_text()
    assert identity.startswith(worker.os.uname().nodename + "-")
    run.assert_called_once_with(
        burst=True, name=identity, max_jobs=2, queue_name="article-analysis"
    )


def test_standalone_worker_rejects_unknown_queue_before_runtime_start(monkeypatch):
    monkeypatch.setattr("sys.argv", ["devfeed-worker", "--queue", "unknown"])
    run = Mock()
    monkeypatch.setattr(worker, "run", run)
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 2
    run.assert_not_called()


@pytest.mark.parametrize("queue_name", ["all", "analysis"])
def test_workers_keep_ai_queue_alias_and_check_readiness_when_dequeueing(monkeypatch, queue_name):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr(settings, "codex_app_server_url", "unix:///run/codex/app-server.sock")
    names = []

    def queue(name="ingestion"):
        names.append(name)
        return SimpleNamespace(connection=SimpleNamespace(close=lambda: None))

    monkeypatch.setattr(worker, "get_queue", queue)
    monkeypatch.setattr(
        worker, "Worker", lambda *a, **kw: SimpleNamespace(name="worker", work=lambda **kw: None)
    )
    worker.run(burst=True, queue_name=queue_name)
    assert names == (
        [
            "ingestion",
            "article-enrichment-fresh",
            "article-enrichment",
            "source-enrichment",
            "images",
            "relationships",
            "article-analysis-fresh",
            "article-analysis",
            "topic-analysis",
            "research-verification",
            "source-analysis",
            "notifications",
        ]
        if queue_name == "all"
        else [
            "relationships",
            "article-analysis-fresh",
            "article-analysis",
            "topic-analysis",
            "research-verification",
            "source-analysis",
        ]
    )
    assert settings.ai_enabled


def test_readiness_backs_off_during_outage_and_checks_each_available_job(monkeypatch):
    from devfeed_aggregator import analysis_worker

    clock, calls = [0.0], []
    reasons = iter([None, "codex_unavailable", None, None])

    def check():
        calls.append(clock[0])
        return next(reasons)

    monkeypatch.setattr(analysis_worker, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    readiness = analysis_worker.CodexReadiness()
    readiness.client = SimpleNamespace(readiness=check)
    assert readiness.ready(pending=False)
    clock[0] = 1
    assert readiness.ready(pending=False)  # Idle can reuse the recent successful probe.
    assert not readiness.ready(pending=True)  # Pending work requires a fresh probe.
    for tick in range(2, 11):
        clock[0] = tick
        assert not readiness.ready(pending=True)
    clock[0] = 11
    assert readiness.ready(pending=True)
    assert readiness.ready(pending=True)
    assert calls == [0, 1, 11, 11]


def test_solver_worker_consumes_only_solver_queue(monkeypatch):
    from devfeed_core.config import SolverService

    monkeypatch.setattr(
        get_settings(),
        "solver_services",
        [SolverService(provider="flaresolverr", url="http://solver.internal")],
    )
    names = []

    def queue(name="ingestion"):
        names.append(name)
        return SimpleNamespace(connection=SimpleNamespace(close=lambda: None))

    monkeypatch.setattr(worker, "get_queue", queue)
    monkeypatch.setattr(
        worker, "Worker", lambda *a, **kw: SimpleNamespace(name="solver", work=lambda **kw: None)
    )
    worker.run(burst=True, queue_name="solver")
    assert names == ["solver"]


@pytest.mark.parametrize(
    "queue_name",
    [
        "article-analysis",
        "topic-analysis",
        "research-verification",
        "source-analysis",
        "relationships",
        "notifications",
        "ingestion",
        "source-discovery",
        "article-enrichment",
        "source-enrichment",
        "images",
    ],
)
def test_dedicated_worker_consumes_only_requested_queue(monkeypatch, queue_name):
    names = []
    monkeypatch.setattr(
        worker,
        "get_queue",
        lambda name="ingestion": (
            names.append(name) or SimpleNamespace(connection=SimpleNamespace(close=lambda: None))
        ),
    )
    monkeypatch.setattr(
        worker, "Worker", lambda *a, **kw: SimpleNamespace(name="dedicated", work=lambda **kw: None)
    )
    worker.run(burst=True, queue_name=queue_name)
    assert names == (
        [queue_name + "-fresh", queue_name]
        if queue_name in {"article-analysis", "article-enrichment"}
        else [queue_name]
    )


@pytest.mark.parametrize("queue_name", ["all", "background"])
def test_managed_images_are_reserved_for_credentialed_images_worker(monkeypatch, queue_name):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "image_storage_enabled", True)
    names = []
    monkeypatch.setattr(
        worker,
        "get_queue",
        lambda name="ingestion": (
            names.append(name) or SimpleNamespace(connection=SimpleNamespace(close=lambda: None))
        ),
    )
    monkeypatch.setattr(
        worker, "Worker", lambda *a, **kw: SimpleNamespace(name="generic", work=lambda **kw: None)
    )
    worker.run(burst=True, queue_name=queue_name)
    assert "images" not in names
    assert "ingestion" in names


@pytest.mark.parametrize(
    "pool,expected",
    [
        ("ai-maintenance", ["topic-analysis", "research-verification", "source-analysis"]),
        (
            "pipeline",
            [
                "ingestion",
                "source-discovery",
                "article-enrichment-fresh",
                "article-enrichment",
                "source-enrichment",
            ],
        ),
    ],
)
def test_pooled_workers_keep_round_robin_and_exclude_reserved_queues(monkeypatch, pool, expected):
    from rq.worker import DequeueStrategy

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", False)
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr(settings, "image_storage_enabled", True)
    queues = []
    work = Mock()
    monkeypatch.setattr(
        worker,
        "get_queue",
        lambda name="ingestion": (
            queues.append(name) or SimpleNamespace(connection=SimpleNamespace(close=lambda: None))
        ),
    )
    monkeypatch.setattr(worker, "Worker", lambda *a, **kw: SimpleNamespace(name="pool", work=work))
    worker.run(burst=True, queue_name=pool)
    assert queues == expected
    assert not {
        "notifications",
        "solver",
        "images",
        "relationships",
        "article-analysis",
        "article-analysis-fresh",
    }.intersection(queues)
    assert work.call_args.kwargs["dequeue_strategy"] is DequeueStrategy.ROUND_ROBIN


def test_pooled_topology_covers_every_physical_queue_without_cross_pool_consumers():
    from collections import Counter

    from devfeed_core.worker_queues import QUEUES, worker_queues

    pools = (
        "article-analysis",
        "ai-maintenance",
        "relationships",
        "pipeline",
        "images",
        "notifications",
        "solver",
    )
    coverage = Counter(
        q
        for pool in pools
        for q in worker_queues(pool, ai_enabled=True, notifications_enabled=True)
    )
    assert coverage == Counter(QUEUES)


@pytest.mark.parametrize("pool", ["pipeline", "ai-maintenance"])
def test_cli_accepts_explicit_pool_without_starting_other_consumers(monkeypatch, tmp_path, pool):
    monkeypatch.setattr(worker, "runtime_marker", lambda _: tmp_path / "identity")
    monkeypatch.setattr("sys.argv", ["devfeed-worker", "--queue", pool, "--burst"])
    run = Mock()
    monkeypatch.setattr(worker, "run", run)
    assert worker.main() == 0
    assert run.call_args.kwargs["queue_name"] == pool
