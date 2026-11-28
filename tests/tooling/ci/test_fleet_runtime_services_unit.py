"""Native image smoke fixtures supply private disposable analytical services."""

import importlib.util
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.support.paths import REPO_ROOT

SPEC = importlib.util.spec_from_file_location(
    "fleet_smoke_runtime_test", REPO_ROOT / "scripts/ci/smoke_fleet_contracts.py"
)
assert SPEC and SPEC.loader
smoke_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke_module)


def test_backend_worker_smoke_uses_priorities_and_an_explicit_override(monkeypatch):
    from devfeed_aggregator import worker
    from devfeed_core import config

    instance = smoke_module.Smoke("candidate-backend", "backend")
    instance.redis = "disposable-redis"
    instance.execute = Mock()
    instance.start = Mock(return_value=instance.prefix + "-worker")
    instance.wait_probe = Mock()
    instance.stop = Mock()
    launch = Mock(return_value=0)
    monkeypatch.setattr(worker, "start_queue_worker", launch)
    try:
        instance.workers()
        starts = instance.start.call_args_list
        assert [call.kwargs["env"]["DEVFEED_WORKER_QUEUE"] for call in starts] == [
            "high",
            "default",
            "low",
            "solver",
            "dedicated-ingestion",
        ]
        for index, call in enumerate(starts):
            env = call.kwargs["env"]
            queue = env["DEVFEED_WORKER_QUEUE"]
            monkeypatch.setenv("DEVFEED_JOB_QUEUE_OVERRIDES", env["DEVFEED_JOB_QUEUE_OVERRIDES"])
            settings = config.Settings()
            monkeypatch.setattr(config, "get_settings", lambda settings=settings: settings)
            monkeypatch.setattr("sys.argv", ["devfeed-worker", "--queue", queue])
            assert worker.main() == 0
            assert launch.call_args.args[1] == queue
            assert call.args[0] == smoke_module.CONTRACTS["worker"]["command"]
            assert ("DEVFEED_SOLVER_SERVICES" in env) == (queue == "solver")
            assert call.kwargs["mounts"] == []
            instance.execute.assert_any_call(
                instance.prefix + "-worker", "python", "/fleet/runtime.py", "worker", queue
            )
            assert settings.job_queue_overrides == (
                {"ingestion": "dedicated-ingestion"} if index == 4 else {}
            )
    finally:
        instance.directory.cleanup()


@pytest.mark.parametrize(
    "component,logical,destination",
    [
        ("article-enrichment-worker", "article-enrichment", "dedicated-article-enrichment"),
        ("source-discovery-worker", "source-discovery", "dedicated-source-discovery"),
        ("images-worker", "images", "images"),
    ],
)
def test_native_dedicated_images_have_exclusive_physical_routes(
    monkeypatch, component, logical, destination
):
    from devfeed_aggregator import worker
    from devfeed_core import config

    instance = smoke_module.Smoke("candidate-dedicated", component)
    instance.redis = "disposable-redis"
    instance.execute = Mock()
    instance.start = Mock(return_value=instance.prefix + "-worker")
    instance.wait_probe = Mock()
    instance.stop = Mock()
    launch = Mock(return_value=0)
    monkeypatch.setattr(worker, "start_queue_worker", launch)
    try:
        instance.workers()
        instance.start.assert_called_once()
        call = instance.start.call_args
        assert call.args[0] == smoke_module.CONTRACTS[component]["command"]
        env = call.kwargs["env"]
        assert env["DEVFEED_WORKER_QUEUE"] == destination
        monkeypatch.setenv("DEVFEED_JOB_QUEUE_OVERRIDES", env["DEVFEED_JOB_QUEUE_OVERRIDES"])
        settings = config.Settings()
        assert settings.job_queue_overrides == (
            {} if logical == "images" else {logical: destination}
        )
        monkeypatch.setattr(worker, "get_settings", lambda: settings)
        monkeypatch.setattr("sys.argv", [call.args[0][0]])
        assert worker.queue_worker_main(logical, "Native dedicated worker") == 0
        assert launch.call_args.args[1] == destination
        instance.execute.assert_any_call(
            instance.prefix + "-worker", "python", "/fleet/runtime.py", "worker", destination
        )
    finally:
        instance.directory.cleanup()


@pytest.mark.parametrize(
    "registered,expected,valid",
    [
        (["high"], "high", True),
        (["dedicated-extraction"], "dedicated-extraction", True),
        (["default"], "high", False),
        (["article-enrichment-fresh", "article-enrichment"], "dedicated-extraction", False),
    ],
)
def test_native_worker_probe_requires_exactly_one_expected_physical_queue(
    monkeypatch, registered, expected, valid
):
    spec = importlib.util.spec_from_file_location(
        "fleet_worker_runtime_test", REPO_ROOT / "scripts/ci/fleet_contracts/runtime.py"
    )
    assert spec and spec.loader
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://fixture.invalid:6379/15")
    monkeypatch.setattr("redis.Redis.from_url", lambda _url: object())
    monkeypatch.setattr(
        "rq.Worker.all",
        lambda **_kwargs: [
            SimpleNamespace(name="fleet-native-test", queue_names=lambda: registered)
        ],
    )
    if valid:
        runtime.worker(expected)
    else:
        with pytest.raises(AssertionError):
            runtime.worker(expected)


@pytest.mark.parametrize("postgres_code", [0, 1])
def test_migration_hook_runs_postgres_and_propagates_failure(monkeypatch, postgres_code):
    from devfeed_cli import main
    from devfeed_core import db
    from sqlalchemy import event

    calls = []
    engine = object()
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    listener = Mock()
    monkeypatch.setattr(event, "listen", listener)

    def run(arguments):
        calls.append(arguments)
        return postgres_code

    monkeypatch.setattr(main, "run", run)
    command = smoke_module.CONTRACTS["migrate"]["command"][-1]
    with pytest.raises(SystemExit) as caught:
        exec(compile(command, "fleet-migration-fixture", "exec"), {})
    assert caught.value.code == postgres_code
    assert calls == [["db", "upgrade"]]
    assert listener.call_args.args[:2] == (engine, "connect")


def test_native_runtime_services_use_only_postgres_and_redis(monkeypatch):
    command = Mock()
    monkeypatch.setattr(smoke_module, "command", command)
    instance = smoke_module.Smoke("candidate-admin-api", "admin-api")
    instance.network = instance.prefix
    instance.wait_probe = Mock()
    try:
        instance.services()
        assert set(instance.containers) == {instance.prefix + "-pg", instance.prefix + "-redis"}
        assert not any("CLICKHOUSE" in key for key in instance.env)
        assert not {"backfill", "analytics-importer"} & smoke_module.CONTRACTS.keys()
    finally:
        instance.directory.cleanup()
