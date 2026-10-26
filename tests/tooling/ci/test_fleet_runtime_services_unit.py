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
def test_preparation_requires_history_and_current_cards_and_closes_exporter(monkeypatch, failed):
    import time

    import devfeed_core.analytics as analytics
    import devfeed_core.analytics_observation as observation
    import devfeed_core.analytics_worker as exporter_module
    import devfeed_core.config as config

    state = SimpleNamespace(clock=0.0, cycles=0, current_policies=[])
    settings = SimpleNamespace(
        database_url="postgresql+psycopg://worker:fixture@database.invalid/app_test",
        analytics_backfill_poll_seconds=0,
    )
    closed = Mock()

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
            return {"ready": True, "lag_seconds": 0}

        async def query(self, sql):
            assert sql == "SELECT * FROM analytics_card_status FINAL"
            # A historical ready latch alone cannot complete preparation, and
            # readiness for a prior policy cannot satisfy current coverage.
            policy = "previous" if state.cycles == 1 else "current"
            state.current_policies.append(policy)
            return [{"ready": not failed, "policy": policy, "lag_seconds": 0}]

    class Exporter:
        def __init__(self, client, supplied):
            assert isinstance(client, Client) and supplied is settings

        async def once(self):
            state.cycles += 1
            state.clock += 30
            return False

        close = closed

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(analytics, "AnalyticsClient", Client)
    monkeypatch.setattr(exporter_module, "AnalyticsExporter", Exporter)
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
    closed.assert_called_once_with()
