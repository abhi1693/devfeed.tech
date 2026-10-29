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

    def queue(name="ingestion", **kwargs):
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
        lambda name="ingestion", **kwargs: (
            names.append(name) or SimpleNamespace(connection=SimpleNamespace(close=lambda: None))
        ),
    )
    monkeypatch.setattr(
        worker, "Worker", lambda *a, **kw: SimpleNamespace(name="dedicated", work=lambda **kw: None)
    )
    worker.run(burst=True, queue_name=queue_name)
    assert names == [queue_name]


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("article-analysis-fresh", "high"),
        ("article-enrichment-fresh", "high"),
        ("article-analysis", "default"),
        ("ingestion", "default"),
        ("notifications", "low"),
        ("images", "images"),
        ("solver", "solver"),
        ("partner-evaluation", "default"),
        ("recommendations", "high"),
    ],
)
def test_priority_routes_keep_logical_kinds_separate_from_transport(monkeypatch, kind, expected):
    monkeypatch.setenv("DEVFEED_JOB_QUEUE_OVERRIDES", "{}")
    from devfeed_core.config import Settings
    from devfeed_core.worker_queues import configured_queue

    settings = Settings(job_queue_overrides={})
    assert configured_queue(kind, settings) == expected


def test_production_routing_defaults_to_priority(monkeypatch):
    from devfeed_core.config import Settings
    from devfeed_core.worker_queues import configured_queue, physical_queues

    monkeypatch.setenv("DEVFEED_JOB_QUEUE_OVERRIDES", "{}")
    settings = Settings()
    assert configured_queue("notifications", settings) == "low"
    assert physical_queues(settings) == ("high", "default", "low", "solver", "images")


def test_queue_overrides_cover_fresh_lane_and_custom_workers():
    from devfeed_core.config import Settings
    from devfeed_core.worker_queues import configured_queue, physical_queues, worker_queues

    settings = Settings(
        job_queue_overrides={
            "article-analysis": "article-ai",
            "article-analysis-fresh": "urgent-ai",
            "notifications": "default",
        },
    )
    assert configured_queue("article-analysis", settings) == "article-ai"
    assert configured_queue("article-analysis-fresh", settings) == "urgent-ai"
    assert configured_queue("notifications", settings) == "default"
    assert worker_queues("article-ai", settings=settings) == ["article-ai"]
    assert len(physical_queues(settings)) == len(set(physical_queues(settings)))
    settings.job_queue_overrides.pop("article-analysis-fresh")
    assert configured_queue("article-analysis-fresh", settings) == "article-ai"


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown-kind": "default"},
        {"ingestion": "bad:name"},
        {"ingestion": "a" * 65},
        {"ingestion": "images"},
        {"images": "high"},
        {"solver": "low"},
        {"images": "solver"},
        {"solver": "custom", "notifications": "custom"},
    ],
)
def test_invalid_or_credential_mixing_queue_routes_fail_configuration(monkeypatch, overrides):
    monkeypatch.setenv("DEVFEED_JOB_QUEUE_OVERRIDES", "{}")
    from devfeed_core.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(job_queue_overrides=overrides)


@pytest.mark.parametrize(
    "selector",
    [
        "source-discovery",
        "article-analysis",
        "article-enrichment",
        "ingestion",
        "relationships",
        "source-analysis",
        "background",
        "analysis",
    ],
)
def test_priority_startup_rejects_old_consumers_that_would_receive_unrelated_jobs(
    monkeypatch, selector
):
    settings = get_settings()
    monkeypatch.setattr(settings, "job_queue_overrides", {})
    queue = Mock()
    runtime = Mock()
    monkeypatch.setattr(worker, "get_queue", queue)
    monkeypatch.setattr(worker, "Worker", runtime)
    with pytest.raises(ValueError, match="Unknown worker queue"):
        worker.run(burst=True, queue_name=selector)
    queue.assert_not_called()
    runtime.assert_not_called()


@pytest.mark.parametrize("selector", ["high", "default", "low", "custom"])
def test_explicit_physical_selectors_are_generic_runtime_opt_in(selector):
    from devfeed_core.config import Settings
    from devfeed_core.worker_queues import worker_queues

    settings = Settings(job_queue_overrides={"ingestion": "custom"})
    assert worker_queues(selector, settings=settings) == [selector]


def test_dedicated_fresh_selector_does_not_subscribe_to_shared_ordinary_lane():
    from devfeed_core.config import Settings
    from devfeed_core.worker_queues import worker_queues

    settings = Settings(job_queue_overrides={"article-analysis-fresh": "fresh-ai"})
    assert worker_queues("fresh-ai", settings=settings) == ["fresh-ai"]
