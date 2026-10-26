"""Native image smoke fixtures supply private disposable analytical services."""

import importlib.util
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_preparation_refreshes_operational_coverage_after_slow_analytics_backfill(monkeypatch):
    import time

    import devfeed_admin_api.overview_projection as reports
    import devfeed_admin_api.pipeline_reads as reads
    import devfeed_admin_api.projection_worker as worker
    import devfeed_core.analytics as analytics
    import devfeed_core.analytics_worker as exporter_module
    import devfeed_core.config as config
    import devfeed_core.db as database
    import sqlalchemy.orm as orm

    state = SimpleNamespace(clock=0.0, fresh=False, requested=set())
    settings = SimpleNamespace(
        read_model_database_url="reserved", analytics_backfill_poll_seconds=1
    )
    settings.model_copy = lambda **_kwargs: settings
    engine = SimpleNamespace(dispose=Mock())

    class Session:
        def get(self, _model, key):
            assert key in state.requested
            return SimpleNamespace(policy_version="current")

    class Factory:
        def __call__(self):
            return nullcontext(Session())

        def begin(self):
            return self()

    class Client:
        def __init__(self, _settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def initialize(self):
            pass

        async def status(self):
            return {"ready": True, "lag_seconds": 0}

    class Exporter:
        def __init__(self, *_args):
            pass

        async def once(self):
            state.clock += 75
            state.fresh = False
            return False

    def drain(**_kwargs):
        state.fresh = True

    def publish(*_args, **_kwargs):
        assert state.fresh, "Slow analytical backfill left operational reports too stale to publish"
        return len(state.requested)

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "create_database_engine", lambda _settings: engine)
    monkeypatch.setattr(orm, "sessionmaker", lambda *_args, **_kwargs: Factory())
    monkeypatch.setattr(analytics, "AnalyticsClient", Client)
    monkeypatch.setattr(exporter_module, "AnalyticsExporter", Exporter)
    monkeypatch.setattr(worker, "run", drain)
    monkeypatch.setattr(
        reads,
        "serving_projection",
        lambda _session: (
            reports.pipeline_policy_version(),
            SimpleNamespace(status="fresh", lag_seconds=0 if state.fresh else 75),
        ),
    )
    monkeypatch.setattr(reports, "report_policy_version", lambda: "current")
    monkeypatch.setattr(
        reports,
        "request_refresh",
        lambda _session, group, window, _now: state.requested.add((group, window.key)),
    )
    monkeypatch.setattr(reports, "refresh_overview_projections", publish)
    monkeypatch.setattr(time, "monotonic", lambda: state.clock)
    command = smoke_module.CONTRACTS["backfill"]["command"][-1]
    exec(compile(command, "fleet-preparation-fixture", "exec"), {})
    assert len(state.requested) == 11
    assert {group for group, _ in state.requested} == {
        "operations",
        "publishing",
        "audience",
        "processing",
        "inference",
        "coverage",
    }
    assert ("operations", "current") in state.requested
    engine.dispose.assert_called_once_with()
