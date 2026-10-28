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
    instance.inbox = Mock(return_value=(["inbox-mount"], {"DEVFEED_NOTIFICATIONS_ENABLED": "true"}))
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
            assert (
                call.args[0]
                == smoke_module.CONTRACTS["notification-worker" if queue == "low" else "worker"][
                    "command"
                ]
            )
            assert ("DEVFEED_SOLVER_SERVICES" in env) == (queue == "solver")
            assert ("DEVFEED_NOTIFICATIONS_ENABLED" in env) == (queue == "low")
            assert call.kwargs["mounts"] == (["inbox-mount"] if queue == "low" else [])
            instance.execute.assert_any_call(
                instance.prefix + "-worker", "python", "/fleet/runtime.py", "worker", queue
            )
            assert settings.job_queue_overrides == (
                {"ingestion": "dedicated-ingestion"} if index == 4 else {}
            )
        instance.inbox.assert_called_once_with("worker")
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


@pytest.mark.parametrize("postgres_code,analytics_code", [(0, 0), (1, None), (0, 1)])
def test_migration_hook_orders_both_schemas_and_propagates_failure(
    monkeypatch, postgres_code, analytics_code
):
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
        return postgres_code if arguments == ["db", "upgrade"] else analytics_code

    monkeypatch.setattr(main, "run", run)
    command = smoke_module.CONTRACTS["migrate"]["command"][-1]
    with pytest.raises(SystemExit) as caught:
        exec(compile(command, "fleet-migration-fixture", "exec"), {})
    assert caught.value.code == (postgres_code or analytics_code)
    assert calls == (
        [["db", "upgrade"]] if postgres_code else [["db", "upgrade"], ["analytics", "upgrade"]]
    )
    assert listener.call_args.args[:2] == (engine, "connect")


def test_required_clickhouse_is_private_authenticated_bounded_and_owned(monkeypatch):
    command = Mock()
    monkeypatch.setattr(smoke_module, "command", command)
    instance = smoke_module.Smoke("candidate-admin-api", "admin-api")
    instance.network = instance.prefix
    instance.wait_probe = Mock()
    try:
        instance.services()
        clickhouse = instance.prefix + "-clickhouse"
        runs = [call.args for call in command.call_args_list]
        launch = next(args for args in runs if clickhouse in args)
        assert launch[:3] == ("docker", "run", "-d")
        assert "--publish" not in launch and "-p" not in launch
        assert launch[launch.index("--network") + 1] == instance.network
        assert launch[launch.index("--cpus") + 1] == "2"
        assert launch[launch.index("--memory") + 1] == "2g"
        assert "CLICKHOUSE_USER=ci" in launch
        assert "CLICKHOUSE_PASSWORD=disposable-analytics-key" in launch
        assert launch[-1] == smoke_module.CLICKHOUSE
        assert clickhouse in instance.containers
        assert instance.env["DEVFEED_CLICKHOUSE_URL"] == f"http://{clickhouse}:8123"
        assert instance.env["DEVFEED_CLICKHOUSE_DATABASE"].endswith("_test")
        assert instance.env["DEVFEED_CLICKHOUSE_USERNAME"] == "ci"
        instance.wait_probe.assert_any_call(
            clickhouse,
            [
                "clickhouse-client",
                "--user",
                "ci",
                "--password",
                "disposable-analytics-key",
                "--database",
                "fleet_native_test",
                "--query",
                "SELECT 1",
            ],
            seconds=60,
        )
    finally:
        instance.directory.cleanup()


@pytest.mark.parametrize("failed", [False, True])
def test_preparation_requires_direct_history_and_current_card_policy(monkeypatch, failed):
    import time

    import devfeed_core.analytics as analytics
    import devfeed_core.analytics_observation as observation
    import devfeed_core.analytics_worker as worker_module
    import devfeed_core.config as config

    state = SimpleNamespace(clock=0.0, cycles=0, current_policies=[])
    settings = SimpleNamespace(
        database_url="postgresql+psycopg://worker:fixture@database.invalid/app_test",
        analytics_backfill_poll_seconds=0,
    )

    class Client:
        def __init__(self, supplied):
            assert supplied is settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def initialize(self):
            pass

        async def status(self):
            policy = "previous" if state.cycles == 1 else "current"
            state.current_policies.append(policy)
            return {"ready": not failed, "policy": policy, "lag_seconds": 0}

    class ImportWorker:
        def __init__(self, client, supplied):
            assert isinstance(client, Client) and supplied is settings

        async def once(self):
            state.cycles += 1
            state.clock += 30
            return False

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(analytics, "AnalyticsClient", Client)
    monkeypatch.setattr(worker_module, "AnalyticsImportWorker", ImportWorker)
    monkeypatch.setattr(observation, "pipeline_policy_version", lambda supplied: "current")
    monkeypatch.setattr(time, "monotonic", lambda: state.clock)
    command = smoke_module.CONTRACTS["backfill"]["command"][-1]
    if failed:
        with pytest.raises(RuntimeError, match="coverage is incomplete"):
            exec(compile(command, "fleet-preparation-fixture", "exec"), {})
        assert state.cycles == 3
    else:
        exec(compile(command, "fleet-preparation-fixture", "exec"), {})
        assert state.cycles == 2
    assert state.current_policies[:2] == ["previous", "current"]
