"""Compose fixtures must not inherit an operator's private configuration."""

import importlib.util
import re
import xml.etree.ElementTree as ET
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
    ],
    ids=["x-pixel", "partner-placement", "clickhouse", "ch-ui"],
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
            returncode=0, stdout='{"services":{"analytics-importer":{"cpus":' + limit + "}}}"
        ),
    )
    services = check_compose.render({"POSTGRES_PASSWORD": "fixture-only-password"})["services"]
    assert (services["analytics-importer"]["cpus"] == Decimal("0.5")) is compliant


@pytest.mark.parametrize("limited", [False, True])
def test_all_worker_pools_share_lightweight_parent_probe_and_sufficient_shutdown_time(limited):
    from devfeed_core.job_definitions import JOB_DEFINITIONS

    options = {"POSTGRES_PASSWORD": "fixture-only-password", "COMPOSE_PROFILES": "workers,ai"}
    if limited:
        options.update(DEVFEED_WORKER_CPUS="1.5", DEVFEED_WORKER_MEMORY_LIMIT="1g")
    services = check_compose.render(options)["services"]
    workers = {
        name: value
        for name, value in services.items()
        if name == "worker" or name == "codex-client" or name.endswith("-worker")
    }
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
        assert grace_seconds >= max(item.timeout for item in JOB_DEFINITIONS.values()) + 30, name
        assert worker.get("cpus", 0) == (Decimal("1.5") if limited else 0), name
        assert int(worker.get("mem_limit", 0)) == (1024**3 if limited else 0), name
        if worker["command"][0] == "sh":
            assert "exec devfeed-worker --queue" in worker["command"][-1], name
    assert all(
        "DEVFEED_WORKER_HEALTH_PATH" not in value.get("environment", {})
        for name, value in services.items()
        if name not in workers
    )


@pytest.mark.parametrize(
    "key", ["DEVFEED_CLICKHOUSE_READER_PASSWORD", "DEVFEED_CLICKHOUSE_INGEST_PASSWORD"]
)
def test_compose_requires_private_role_passwords(key):
    with pytest.raises(RuntimeError, match="Compose fixture failed to render"):
        check_compose.render(
            {
                "POSTGRES_PASSWORD": "fixture-postgres-password",
                "DEVFEED_CLICKHOUSE_PASSWORD": "fixture-operator-password",
                key: "",
            }
        )


@pytest.mark.parametrize("build", [False, True], ids=["registry", "local-build"])
def test_private_mcp_routing_is_confined_to_reader(build):
    check_compose.check_private_mcp_routing(
        {"POSTGRES_PASSWORD": "fixture-only-password", "COMPOSE_PROFILES": "mcp"},
        {"DEVFEED_MCP_PUBLIC_URL": "https://agents.example.test/mcp"},
        build=build,
    )


@pytest.mark.parametrize("build", [False, True], ids=["registry", "local-build"])
def test_clickhouse_credentials_and_resources_are_isolated_from_interactive_services(build):
    options = {
        "POSTGRES_PASSWORD": "fixture-postgres-password",
        "DEVFEED_CLICKHOUSE_PASSWORD": "fixture-separate-analytics-password",
        "DEVFEED_CLICKHOUSE_INGEST_PASSWORD": "fixture-separate-exporter-password",
        "DEVFEED_CLICKHOUSE_READER_PASSWORD": "fixture-separate-reader-password",
        "DEVFEED_CLICKHOUSE_PUBLIC_PASSWORD": "fixture-separate-public-password",
        "DEVFEED_CLICKHOUSE_USER_PASSWORD": "fixture-separate-user-password",
        "DEVFEED_CLICKHOUSE_PARTNER_PASSWORD": "fixture-separate-partner-password",
        "DEVFEED_CLICKHOUSE_ADMIN_PASSWORD": "fixture-separate-admin-password",
        "DEVFEED_CLICKHOUSE_WORKER_PASSWORD": "fixture-separate-worker-password",
        "DEVFEED_CLICKHOUSE_DATABASE": "analytics_test",
        "DEVFEED_CLICKHOUSE_CPUS": "1.5",
        "DEVFEED_CLICKHOUSE_MEMORY_LIMIT": "3g",
        "DEVFEED_CLICKHOUSE_MAX_THREADS": "1",
    }
    services = check_compose.render(options, build=build)["services"]
    clickhouse = services["clickhouse"]
    assert (
        clickhouse["environment"]["CLICKHOUSE_PASSWORD"] == options["DEVFEED_CLICKHOUSE_PASSWORD"]
    )
    assert not clickhouse.get("ports")
    assert set(clickhouse["networks"]) == {"data"}
    assert clickhouse["cpus"] == 1.5
    assert int(clickhouse["mem_limit"]) == 3 * 1024**3
    assert "$$DEVFEED_CLICKHOUSE_INGEST_PASSWORD" in " ".join(clickhouse["healthcheck"]["test"])
    assert "--user devfeed_ingest" in " ".join(clickhouse["healthcheck"]["test"])
    for name, service in services.items():
        environment = service.get("environment", {})
        expected = name in {
            "migrate",
            "analytics-importer",
            "admin-api",
            "api",
            "partner-api",
            "user-api",
            "high-worker",
            "default-worker",
            "low-worker",
            "images-worker",
        }
        assert ("DEVFEED_CLICKHOUSE_PASSWORD" in environment) == expected
        if expected:
            credential = {
                "migrate": "DEVFEED_CLICKHOUSE_INGEST_PASSWORD",
                "analytics-importer": "DEVFEED_CLICKHOUSE_INGEST_PASSWORD",
                "admin-api": "DEVFEED_CLICKHOUSE_ADMIN_PASSWORD",
                "api": "DEVFEED_CLICKHOUSE_PUBLIC_PASSWORD",
                "partner-api": "DEVFEED_CLICKHOUSE_PARTNER_PASSWORD",
                "user-api": "DEVFEED_CLICKHOUSE_USER_PASSWORD",
                "high-worker": "DEVFEED_CLICKHOUSE_WORKER_PASSWORD",
                "default-worker": "DEVFEED_CLICKHOUSE_WORKER_PASSWORD",
                "low-worker": "DEVFEED_CLICKHOUSE_WORKER_PASSWORD",
                "images-worker": "DEVFEED_CLICKHOUSE_WORKER_PASSWORD",
            }[name]
            assert environment["DEVFEED_CLICKHOUSE_PASSWORD"] == options[credential]
            role = {
                "migrate": "devfeed_ingest",
                "analytics-importer": "devfeed_ingest",
                "admin-api": "devfeed_admin",
                "api": "devfeed_public",
                "partner-api": "devfeed_partner",
                "user-api": "devfeed_user",
                "high-worker": "devfeed_worker",
                "default-worker": "devfeed_worker",
                "low-worker": "devfeed_worker",
                "images-worker": "devfeed_worker",
            }[name]
            assert environment["DEVFEED_CLICKHOUSE_USERNAME"] == role
            assert environment["DEVFEED_CLICKHOUSE_MAX_THREADS"] == "1"
        if name not in {"migrate", "analytics-importer", "ch-ui"}:
            assert "clickhouse" not in service.get("depends_on", {})
    assert services["analytics-importer"]["command"] == [
        "python",
        "-m",
        "devfeed_core.analytics_worker",
    ]
    assert services["migrate"]["command"] == [
        "sh",
        "-ec",
        "devfeed db upgrade && exec devfeed analytics upgrade",
    ]
    assert services["analytics-importer"]["environment"]["DEVFEED_CLICKHOUSE_USERNAME"] == (
        "devfeed_ingest"
    )
    assert services["admin-api"]["environment"]["DEVFEED_CLICKHOUSE_USERNAME"] == "devfeed_admin"
    assert (
        clickhouse["environment"]["DEVFEED_CLICKHOUSE_READER_PASSWORD"]
        == (options["DEVFEED_CLICKHOUSE_READER_PASSWORD"])
    )
    assert clickhouse["environment"]["DEVFEED_CLICKHOUSE_READER_GRANT"] == (
        "GRANT SELECT ON analytics_test.*"
    )
    assert clickhouse["environment"]["DEVFEED_CLICKHOUSE_PUBLIC_EVENTS_PARTNER_GRANT"] == (
        "GRANT INSERT ON analytics_test.analytics_partner_events"
    )
    assert clickhouse["environment"]["DEVFEED_CLICKHOUSE_PUBLIC_EVENTS_SEARCH_GRANT"] == (
        "GRANT SELECT, INSERT ON analytics_test.analytics_search_events"
    )
    assert clickhouse["environment"]["DEVFEED_CLICKHOUSE_PARTNER_EVENTS_PARTNER_GRANT"] == (
        "GRANT SELECT ON analytics_test.analytics_partner_events"
    )
    assert clickhouse["environment"]["DEVFEED_CLICKHOUSE_INGEST_GRANT"] == (
        "GRANT SELECT, INSERT, CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW, "
        "ALTER TABLE, SYSTEM MERGES ON analytics_test.*"
    )
    assert (
        clickhouse["environment"]["DEVFEED_CLICKHOUSE_INGEST_PASSWORD"]
        == options["DEVFEED_CLICKHOUSE_INGEST_PASSWORD"]
    )
    assert services["analytics-importer"]["cpus"] == 0.5
    assert int(services["analytics-importer"]["mem_limit"]) == 512 * 1024**2
    assert services["analytics-importer"]["healthcheck"]["timeout"] == "10s"


def test_server_profiles_reserve_import_capacity_across_reader_replicas():
    services = check_compose.render({"POSTGRES_PASSWORD": "fixture-only-password"})["services"]
    environment = services["clickhouse"]["environment"]
    readers = int(environment["DEVFEED_CLICKHOUSE_READER_MAX_QUERIES"])
    writers = int(environment["DEVFEED_CLICKHOUSE_INGEST_MAX_QUERIES"])
    assert int(environment["DEVFEED_CLICKHOUSE_SERVER_MAX_SELECTS"]) >= readers + 1
    assert int(environment["DEVFEED_CLICKHOUSE_SERVER_MAX_INSERTS"]) == 2
    assert int(environment["DEVFEED_CLICKHOUSE_SERVER_MAX_QUERIES"]) > readers + writers
    assert (
        int(environment["DEVFEED_CLICKHOUSE_READER_MAX_MEMORY_BYTES"])
        + int(environment["DEVFEED_CLICKHOUSE_INGEST_MAX_MEMORY_BYTES"])
        < int(environment["DEVFEED_CLICKHOUSE_SERVER_MAX_MEMORY_BYTES"])
        < int(services["clickhouse"]["mem_limit"])
    )
    users = ET.parse(REPO_ROOT / "infra/clickhouse/users.xml").getroot()
    assert users.findtext("users/devfeed_ingest/profile") == "devfeed_ingest"
    assert users.findtext("users/devfeed_ingest/access_management") == "0"
    assert users.find("users/devfeed_ingest/grants/query").get("from_env") == (
        "DEVFEED_CLICKHOUSE_INGEST_GRANT"
    )
    assert users.find("users/devfeed_ingest/grants/mutation_status") is None
    assert users.findtext("users/devfeed_reader/profile") == "devfeed_reader"
    assert users.findtext("profiles/devfeed_reader/readonly") == "2"
    assert users.find("users/devfeed_reader/grants/query").get("from_env") == (
        "DEVFEED_CLICKHOUSE_READER_GRANT"
    )
    for profile, role in (("default", "READER"), ("devfeed_ingest", "INGEST")):
        root = users.find(f"profiles/{profile}")
        assert root is not None
        assert root.find("max_concurrent_queries_for_user").get("from_env") == (
            f"DEVFEED_CLICKHOUSE_{role}_MAX_QUERIES"
        )
        assert root.find("max_memory_usage_for_user").get("from_env") == (
            f"DEVFEED_CLICKHOUSE_{role}_MAX_MEMORY_BYTES"
        )
        assert root.find("constraints/max_concurrent_queries_for_user/readonly") is not None
        assert root.find("constraints/max_memory_usage_for_user/readonly") is not None
        for name in ("max_threads", "max_execution_time", "max_memory_usage"):
            assert int(root.findtext(f"constraints/{name}/min")) > 0
            assert root.find(f"constraints/{name}/max").get("from_env") == root.find(name).get(
                "from_env"
            )
    server = ET.parse(REPO_ROOT / "infra/clickhouse/resources.xml").getroot()
    for name, key in (
        ("max_concurrent_queries", "DEVFEED_CLICKHOUSE_SERVER_MAX_QUERIES"),
        ("max_concurrent_select_queries", "DEVFEED_CLICKHOUSE_SERVER_MAX_SELECTS"),
        ("max_concurrent_insert_queries", "DEVFEED_CLICKHOUSE_SERVER_MAX_INSERTS"),
        ("max_server_memory_usage", "DEVFEED_CLICKHOUSE_SERVER_MAX_MEMORY_BYTES"),
    ):
        assert server.find(name).get("from_env") == key


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, "0.0.0.0"),
        ({"DEVFEED_BIND_IP": "192.168.1.101"}, "192.168.1.101"),
        (
            {"DEVFEED_BIND_IP": "192.168.1.101", "DEVFEED_CH_UI_BIND_IP": "127.0.0.1"},
            "127.0.0.1",
        ),
    ],
)
def test_management_ui_inherits_lan_binding_with_an_explicit_override(options, expected):
    services = check_compose.render({"POSTGRES_PASSWORD": "fixture-password", **options})[
        "services"
    ]
    assert services["ch-ui"]["ports"][0]["host_ip"] == expected
    assert services["ch-ui"]["ports"][0]["published"] == "4000"
    assert not services["clickhouse"].get("ports")


@pytest.mark.parametrize("build", [False, True], ids=["registry", "local-build"])
def test_management_ui_has_private_database_access_and_separate_persistent_state(build):
    options = {
        "POSTGRES_PASSWORD": "fixture-postgres-password",
        "DEVFEED_CLICKHOUSE_PASSWORD": "fixture-database-password",
        "DEVFEED_CH_UI_SECRET_KEY": "fixture-separate-ui-encryption-key",
        "DEVFEED_CH_UI_BIND_IP": "127.0.0.2",
        "DEVFEED_CH_UI_PORT": "3499",
        "DEVFEED_CH_UI_URL": "http://localhost:3499",
        "DEVFEED_CH_UI_CPUS": "0.75",
        "DEVFEED_CH_UI_MEMORY_LIMIT": "768m",
    }
    services = check_compose.render(options, build=build)["services"]
    management = services["ch-ui"]
    assert management["ports"][0]["host_ip"] == "127.0.0.2"
    assert management["ports"][0]["published"] == "3499"
    assert management["cpus"] == 0.75
    assert int(management["mem_limit"]) == 768 * 1024**2
    assert set(management["depends_on"]) == {"clickhouse"}
    assert management["depends_on"]["clickhouse"]["condition"] == "service_healthy"
    assert set(management["networks"]) == {"default", "data"}
    environment = management["environment"]
    assert environment == {
        "PORT": "4000",
        "CLICKHOUSE_URL": "http://clickhouse:8123",
        "CONNECTION_NAME": "DevFeed analytics",
        "DATABASE_PATH": "/app/data/ch-ui.db",
        "APP_URL": "http://localhost:3499",
        "APP_SECRET_KEY": options["DEVFEED_CH_UI_SECRET_KEY"],
        "ALLOWED_ORIGINS": "http://localhost:3499",
        "ALLOW_LOGIN_URL": "false",
        "TRUSTED_PROXIES": "none",
    }
    assert options["DEVFEED_CLICKHOUSE_PASSWORD"] not in environment.values()
    assert management["volumes"][0]["source"] == "ch-ui-data"
    assert management["volumes"][0]["target"] == "/app/data"
    assert not management["volumes"][0].get("read_only", False)
    assert management["read_only"] and management["cap_drop"] == ["ALL"]
    assert management["command"] == ["--pid-file", "/run/ch-ui/ch-ui-server.pid"]
    check_compose.check_ch_ui_runtime(management)
    assert management["healthcheck"]["test"][-1] == "http://127.0.0.1:4000/health"
    for name, service in services.items():
        assert "ch-ui" not in service.get("depends_on", {})
        assert ("APP_SECRET_KEY" in service.get("environment", {})) == (name == "ch-ui")


@pytest.fixture
def management_runtime():
    return check_compose.render({"POSTGRES_PASSWORD": "fixture-postgres-password"})["services"][
        "ch-ui"
    ]


@pytest.mark.parametrize("option", ["mode=1777", "uid=0", "gid=0"], ids=["mode", "uid", "gid"])
def test_management_ui_runtime_rejects_public_or_incorrectly_owned_tmpfs(
    management_runtime, option
):
    mounts = management_runtime["tmpfs"]
    index = next(index for index, mount in enumerate(mounts) if mount.startswith("/run/ch-ui:"))
    key = option.partition("=")[0]
    mounts[index] = ",".join(
        option if part.startswith(f"{key}=") else part for part in mounts[index].split(",")
    )
    with pytest.raises(AssertionError):
        check_compose.check_ch_ui_runtime(management_runtime)


@pytest.mark.parametrize("target", ["/run", "/run/ch-ui", "/run/ch-ui/ch-ui-server.pid"])
def test_management_ui_runtime_rejects_volumes_shadowing_its_private_directory(
    management_runtime, target
):
    management_runtime["volumes"].append(
        {"type": "bind", "source": "/host/runtime", "target": target}
    )
    with pytest.raises(AssertionError):
        check_compose.check_ch_ui_runtime(management_runtime)


def test_management_ui_runtime_requires_its_private_tmpfs(management_runtime):
    management_runtime["tmpfs"] = [
        mount for mount in management_runtime["tmpfs"] if not mount.startswith("/run/ch-ui:")
    ]
    with pytest.raises(AssertionError):
        check_compose.check_ch_ui_runtime(management_runtime)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("user", "0:0"),
        ("read_only", False),
        ("command", ["--pid-file", "/app/data/ch-ui-server.pid"]),
    ],
    ids=["root-user", "writable-root", "persistent-pid"],
)
def test_management_ui_runtime_requires_non_root_private_pid_configuration(
    management_runtime, key, value
):
    management_runtime[key] = value
    with pytest.raises(AssertionError):
        check_compose.check_ch_ui_runtime(management_runtime)


@pytest.mark.parametrize("build", [False, True])
def test_priority_compose_reserves_each_queue_capacity_and_keeps_scoped_images(build):
    services = check_compose.render(
        {
            "POSTGRES_PASSWORD": "fixture-only-password",
            "COMPOSE_PROFILES": "workers,ai,legacy-workers,legacy-ai,legacy-dedicated",
            "DEVFEED_JOB_QUEUE_OVERRIDES": '{"notifications":"high"}',
        },
        pooled=True,
        build=build,
    )["services"]
    for name in (
        "worker",
        "codex-client",
        "article-analysis-worker",
        "relationships-worker",
        "ingestion-worker",
        "source-discovery-worker",
        "article-enrichment-worker",
        "source-enrichment-worker",
        "topic-analysis-worker",
        "research-verification-worker",
        "source-analysis-worker",
        "notifications-worker",
    ):
        assert services[name]["scale"] == 0
    for queue, replicas in [("high", 4), ("default", 4), ("low", 1)]:
        name = f"{queue}-worker"
        service = services[name]
        assert service["scale"] == replicas
        assert service["environment"]["DEVFEED_WORKER_QUEUE"] == queue
        assert service["environment"]["DEVFEED_JOB_QUEUE_OVERRIDES"] == '{"notifications":"high"}'
        assert service["environment"]["DEVFEED_CLICKHOUSE_USERNAME"] == "devfeed_worker"
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
    assert "worker" not in services and "article-analysis-worker" not in services


def test_routing_overrides_reach_every_publisher_and_worker_without_frontend_exposure():
    options = {
        "POSTGRES_PASSWORD": "fixture-only-password",
        "DEVFEED_JOB_QUEUE_MODE": "priority",
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
        assert services[name]["environment"]["DEVFEED_JOB_QUEUE_MODE"] == "priority"
        assert (
            services[name]["environment"]["DEVFEED_JOB_QUEUE_OVERRIDES"]
            == options["DEVFEED_JOB_QUEUE_OVERRIDES"]
        )
    for name in ("web", "admin", "partner"):
        assert "DEVFEED_JOB_QUEUE_OVERRIDES" not in services[name]["environment"]
