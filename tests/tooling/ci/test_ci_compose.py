"""Compose fixtures must not inherit an operator's private configuration."""

import importlib.util
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "check_compose_test", REPO_ROOT / "scripts/ci/check_compose.py"
)
check_compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_compose)


@pytest.mark.parametrize(
    "configuration",
    [
        {
            "DEVFEED_X_PIXEL_ENABLED": "true",
            "X_PIXEL_TOKEN": "inherited-test-secret",
            "X_SIGNUP_EVENT_ID": "tw-pc5f8-inherited",
        },
        {
            "DEVFEED_PARTNER_CALLER_KEY": "inherited-test-caller-secret",
            "DEVFEED_PARTNER_CALLER_IP_HEADER": "cf-connecting-ip",
            "DEVFEED_PARTNER_TRACKING_KEY": "inherited-test-tracking-secret",
        },
        {
            "DEVFEED_CLICKHOUSE_PASSWORD": "inherited-private-analytics-secret",
            "DEVFEED_CLICKHOUSE_INGEST_PASSWORD": "inherited-private-exporter-secret",
            "DEVFEED_CLICKHOUSE_READER_PASSWORD": "inherited-private-reader-secret",
            "CLICKHOUSE_PASSWORD": "inherited-private-service-secret",
        },
        {"DEVFEED_CH_UI_SECRET_KEY": "inherited-private-ui-secret"},
        {
            "DEVFEED_DIRECT_DATABASE_URL": (
                "postgresql+psycopg://private:inherited-secret@database.example.test/private"
            )
        },
    ],
    ids=["x-pixel", "partner-placement", "clickhouse", "ch-ui", "direct-database"],
)
def test_compose_render_isolates_inherited_private_configuration(monkeypatch, configuration):
    for key, value in configuration.items():
        monkeypatch.setenv(key, value)
    environments = []

    def run(command, **kwargs):
        environments.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout='{"services": {}}')

    monkeypatch.setattr(check_compose.subprocess, "run", run)
    assert check_compose.render({"POSTGRES_PASSWORD": "fixture-only-password"}) == {"services": {}}
    assert len(environments) == 1
    for key in configuration:
        assert key not in environments[0]


@pytest.mark.parametrize("build", [False, True], ids=["registry", "local-build"])
@pytest.mark.parametrize(
    "options",
    [
        {
            "DEVFEED_DATABASE_URL": "postgresql+psycopg://fixture@pooler.example.test/devfeed_test",
            "DEVFEED_DIRECT_DATABASE_URL": (
                "postgresql+psycopg://fixture@database.example.test/devfeed_test"
            ),
        },
        {"DEVFEED_DATABASE_URL": "postgresql+psycopg://fixture@pooler.example.test/devfeed_test"},
        {
            "DEVFEED_DATABASE_URL": "postgresql+psycopg://fixture@pooler.example.test/devfeed_test",
            "DEVFEED_DIRECT_DATABASE_URL": "",
        },
        {
            "DEVFEED_DIRECT_DATABASE_URL": (
                "postgresql+psycopg://fixture@database.example.test/devfeed_test"
            )
        },
        {},
        {"DEVFEED_DATABASE_URL": "", "DEVFEED_DIRECT_DATABASE_URL": ""},
    ],
    ids=["direct", "normal-only", "empty-direct", "direct-with-default", "defaults", "empty"],
)
def test_session_affine_database_override_is_confined_to_lock_owners(build, options):
    services = check_compose.render(
        {
            "POSTGRES_PASSWORD": "fixture-only-password",
            "COMPOSE_PROFILES": "workers,ai,notifications,search,mcp",
            **options,
        },
        build=build,
    )["services"]
    normal_url = options.get("DEVFEED_DATABASE_URL") or (
        "postgresql+psycopg://devfeed:fixture-only-password@postgres:5432/devfeed"
    )
    direct_url = options.get("DEVFEED_DIRECT_DATABASE_URL") or normal_url
    lock_owners = {"migrate"}
    database_services = {
        name: service["environment"]["DEVFEED_DATABASE_URL"]
        for name, service in services.items()
        if "DEVFEED_DATABASE_URL" in service.get("environment", {})
    }
    assert set(database_services) == {
        *lock_owners,
        "api",
        "admin-api",
        "user-api",
        "partner-api",
        "scheduler",
        "high-worker",
        "default-worker",
        "low-worker",
        "images-worker",
        "search-setup",
        "search-indexer",
    }
    for name, actual in database_services.items():
        assert actual == (direct_url if name in lock_owners else normal_url), name
    for name, service in services.items():
        environment = service.get("environment", {})
        assert "DEVFEED_DIRECT_DATABASE_URL" not in environment, name
        if direct_url != normal_url and name not in lock_owners:
            assert direct_url not in environment.values(), name


@pytest.mark.parametrize(
    ("limit", "compliant"),
    [("0.50", True), ("5e-1", True), ("0.50000000000000001", False)],
    ids=["decimal", "exponent", "exceeds-exact-budget"],
)
def test_compose_render_preserves_exact_fractional_cpu_budget(monkeypatch, limit, compliant):
    monkeypatch.setattr(
        check_compose.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout='{"services":{"high-worker":{"cpus":' + limit + "}}}"
        ),
    )
    services = check_compose.render({"POSTGRES_PASSWORD": "fixture-only-password"})["services"]
    assert (services["high-worker"]["cpus"] == Decimal("0.5")) is compliant


@pytest.mark.parametrize("limited", [False, True])
def test_all_worker_pools_share_lightweight_parent_probe_and_sufficient_shutdown_time(limited):
    from devfeed_core.job_definitions import JOB_DEFINITIONS

    options = {"POSTGRES_PASSWORD": "fixture-only-password", "COMPOSE_PROFILES": "workers,ai"}
    if limited:
        options.update(
            DEVFEED_WORKER_CPUS="1.5",
            DEVFEED_WORKER_MEMORY_LIMIT="1g",
        )
    services = check_compose.render(options)["services"]
    workers = {name: value for name, value in services.items() if name.endswith("-worker")}
    assert set(workers) == {"high-worker", "default-worker", "low-worker", "images-worker"}
    for name, worker in workers.items():
        assert worker["healthcheck"]["test"] == [
            "CMD",
            "devfeed-worker-health",
        ], name
        assert worker["environment"]["DEVFEED_WORKER_HEALTH_PATH"] == (
            "/tmp/devfeed-worker-health"
        ), name
        grace_seconds = sum(
            int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
            for value, unit in re.findall(r"(\d+)([hms])", worker["stop_grace_period"])
        )
        assert grace_seconds >= max(item.timeout for item in JOB_DEFINITIONS.values()) + 60, name
        assert worker.get("cpus", 0) == (Decimal("1.5") if limited else 0), name
        assert int(worker.get("mem_limit", 0)) == (1024**3 if limited else 0), name
        if worker["command"][0] == "sh":
            assert "exec devfeed-worker --queue" in worker["command"][-1], name
    assert all(
        not {"DEVFEED_WORKER_HEALTH_PATH", "DEVFEED_ANALYTICS_WORKER_DRAIN_SECONDS"}
        & value.get("environment", {}).keys()
        for name, value in services.items()
        if name not in workers
    )


@pytest.mark.parametrize("build", [False, True], ids=["registry", "local-build"])
def test_private_mcp_routing_is_confined_to_reader(build):
    check_compose.check_private_mcp_routing(
        {"POSTGRES_PASSWORD": "fixture-only-password", "COMPOSE_PROFILES": "mcp"},
        {"DEVFEED_MCP_PUBLIC_URL": "https://agents.example.test/mcp"},
        build=build,
    )


@pytest.mark.parametrize("build", [False, True])
def test_runtime_services_have_no_warehouse_dependency(build):
    services = check_compose.render({"POSTGRES_PASSWORD": "fixture-only"}, build=build)["services"]
    assert not {"clickhouse", "analytics-importer", "ch-ui"} & services.keys()
    for service in services.values():
        assert "clickhouse" not in service.get("depends_on", {})
        assert not any("CLICKHOUSE" in key for key in service.get("environment", {}))


@pytest.mark.parametrize("build", [False, True])
def test_priority_compose_reserves_each_queue_capacity_and_keeps_scoped_images(build):
    services = check_compose.render(
        {
            "POSTGRES_PASSWORD": "fixture-only-password",
            "COMPOSE_PROFILES": "ai",
            "DEVFEED_JOB_QUEUE_OVERRIDES": '{"notifications":"high"}',
        },
        worker_overrides=True,
        build=build,
    )["services"]
    assert {name for name in services if name.endswith("-worker")} == {
        "high-worker",
        "default-worker",
        "low-worker",
        "images-worker",
    }
    for queue, replicas in [("high", 4), ("default", 4), ("low", 1)]:
        name = f"{queue}-worker"
        service = services[name]
        assert service["scale"] == replicas
        assert service["environment"]["DEVFEED_WORKER_QUEUE"] == queue
        assert service["environment"]["DEVFEED_JOB_QUEUE_OVERRIDES"] == '{"notifications":"high"}'
        assert "DEVFEED_IMAGE_STORAGE_SECRET_KEY" not in service["environment"]
        assert not service["environment"].get("DEVFEED_SOLVER_SERVICES")
        assert service["entrypoint"] == ["python", "/opt/devfeed/chimely-consumer.py", "worker"]
        assert {v["source"] for v in service["volumes"] if v["type"] == "volume"} == {
            "chimely-worker-credentials",
            "codex-socket",
        }
        if build:
            assert service["image"] == f"devfeed/{name}:local"
            assert service["build"]["dockerfile"] == "Dockerfile"
    assert services["images-worker"]["scale"] == 1


def test_priority_deployments_have_independent_replica_overrides():
    services = check_compose.render(
        {
            "POSTGRES_PASSWORD": "fixture-only-password",
            "DEVFEED_HIGH_WORKER_REPLICAS": "6",
            "DEVFEED_DEFAULT_WORKER_REPLICAS": "3",
            "DEVFEED_LOW_WORKER_REPLICAS": "2",
        }
    )["services"]
    assert {
        queue: services[f"{queue}-worker"]["scale"] for queue in ("high", "default", "low")
    } == {"high": 6, "default": 3, "low": 2}
    assert {name for name in services if name.endswith("-worker")} == {
        "high-worker",
        "default-worker",
        "low-worker",
        "images-worker",
    }


def test_routing_overrides_reach_every_publisher_and_worker_without_frontend_exposure():
    options = {
        "POSTGRES_PASSWORD": "fixture-only-password",
        "DEVFEED_JOB_QUEUE_OVERRIDES": '{"recommendations":"recommendations"}',
    }
    services = check_compose.render(options)["services"]
    for name in (
        "api",
        "admin-api",
        "partner-api",
        "user-api",
        "scheduler",
        "high-worker",
        "default-worker",
        "low-worker",
    ):
        assert (
            services[name]["environment"]["DEVFEED_JOB_QUEUE_OVERRIDES"]
            == options["DEVFEED_JOB_QUEUE_OVERRIDES"]
        )
    for name in ("web", "admin", "partner"):
        assert "DEVFEED_JOB_QUEUE_OVERRIDES" not in services[name]["environment"]
