"""Compose fixtures must not inherit an operator's private configuration."""

import importlib.util
import xml.etree.ElementTree as ET
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
        expected = name in {"analytics-exporter", "admin-api"}
        assert ("DEVFEED_CLICKHOUSE_PASSWORD" in environment) == expected
        if expected:
            credential = (
                "DEVFEED_CLICKHOUSE_INGEST_PASSWORD"
                if name == "analytics-exporter"
                else "DEVFEED_CLICKHOUSE_PASSWORD"
            )
            assert environment["DEVFEED_CLICKHOUSE_PASSWORD"] == options[credential]
            assert environment["DEVFEED_CLICKHOUSE_MAX_THREADS"] == "1"
        if name not in {"analytics-exporter", "ch-ui"}:
            assert "clickhouse" not in service.get("depends_on", {})
    assert services["analytics-exporter"]["command"] == [
        "python",
        "-m",
        "devfeed_core.analytics_worker",
    ]
    assert services["analytics-exporter"]["environment"]["DEVFEED_CLICKHOUSE_USERNAME"] == (
        "devfeed_ingest"
    )
    assert (
        clickhouse["environment"]["DEVFEED_CLICKHOUSE_INGEST_PASSWORD"]
        == options["DEVFEED_CLICKHOUSE_INGEST_PASSWORD"]
    )
    assert services["analytics-exporter"]["cpus"] == 0.5
    assert int(services["analytics-exporter"]["mem_limit"]) == 512 * 1024**2
    assert services["analytics-exporter"]["healthcheck"]["timeout"] == "10s"


def test_server_profiles_reserve_capacity_for_exporter_across_reader_replicas():
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
    assert management["command"] == ["--pid-file", "/tmp/ch-ui-server.pid"]
    assert management["healthcheck"]["test"][-1] == "http://127.0.0.1:4000/health"
    for name, service in services.items():
        assert "ch-ui" not in service.get("depends_on", {})
        assert ("APP_SECRET_KEY" in service.get("environment", {})) == (name == "ch-ui")
