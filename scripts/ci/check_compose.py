"""Render isolated Compose fixtures to check networking and credential routing."""

import json
import os
import runpy
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


def render(values: dict[str, str], *, build: bool = False, worker_overrides: bool = False) -> dict:
    # Never read the developer's .env or inherited application credentials.
    values = {
        "DEVFEED_CLICKHOUSE_READER_PASSWORD": "fixture-only-reader-password",
        "DEVFEED_CLICKHOUSE_INGEST_PASSWORD": "fixture-only-ingest-password",
        "DEVFEED_CLICKHOUSE_PUBLIC_PASSWORD": "fixture-only-public-password",
        "DEVFEED_CLICKHOUSE_USER_PASSWORD": "fixture-only-user-password",
        "DEVFEED_CLICKHOUSE_PARTNER_PASSWORD": "fixture-only-partner-password",
        "DEVFEED_CLICKHOUSE_ADMIN_PASSWORD": "fixture-only-admin-password",
        "DEVFEED_CLICKHOUSE_WORKER_PASSWORD": "fixture-only-worker-password",
        **values,
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                "DEVFEED_",
                "COMPOSE_",
                "POSTGRES_",
                "CHIMELY_",
                "CLICKHOUSE_",
                "CODEX_",
                "OPENAI_",
                "OTEL_",
                "GOOGLE_ANALYTICS_ID",
                "X_PIXEL_TOKEN",
                "X_SIGNUP_EVENT_ID",
            )
        )
    }
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / ".env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        command = ["docker", "compose", "--env-file", str(env_file), "-f", "compose.yaml"]
        if worker_overrides:
            command += ["-f", "compose.workers.yaml"]
        if build:
            command += ["-f", "compose.build.yaml"]
        result = subprocess.run(
            [*command, "config", "--format", "json"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        raise RuntimeError("Compose fixture failed to render; configuration output withheld")
    # Resource budgets must not accept a different decimal rounded into a binary float.
    return json.loads(result.stdout, parse_float=Decimal)


def check_private_mcp_routing(
    registry_fixtures: dict[str, str], mcp_options: dict[str, str], *, build: bool
) -> None:
    """Keep the private MCP fetch address confined to the reader service."""
    private_mcp = render(
        {
            **registry_fixtures,
            **mcp_options,
            "DEVFEED_MCP_INTERNAL_URL": "http://mcp:8003/mcp",
        },
        build=build,
    )["services"]
    assert private_mcp["web"]["environment"]["DEVFEED_MCP_INTERNAL_URL"] == ("http://mcp:8003/mcp")
    assert (
        private_mcp["web"]["environment"]["DEVFEED_MCP_PUBLIC_URL"]
        == (mcp_options["DEVFEED_MCP_PUBLIC_URL"])
    )
    for name, service in private_mcp.items():
        assert ("DEVFEED_MCP_INTERNAL_URL" in service.get("environment", {})) == (name == "web")


def check_ch_ui_runtime(management: dict) -> None:
    """Require an ephemeral PID directory writable only by the pinned image user."""
    directory = PurePosixPath("/run/ch-ui")
    assert management["user"] == "100:101"
    assert management["read_only"]
    assert management["command"] == ["--pid-file", str(directory / "ch-ui-server.pid")]
    mounts = [mount for mount in management["tmpfs"] if mount.partition(":")[0] == str(directory)]
    assert mounts == [f"{directory}:size=1m,mode=0700,uid=100,gid=101"]
    for volume in management.get("volumes", []):
        target = PurePosixPath(volume["target"])
        assert (
            target != directory
            and target not in directory.parents
            and directory not in target.parents
        )


def check() -> None:
    base = {"POSTGRES_PASSWORD": "a" * 64}
    registry_fixtures = {
        **base,
        "COMPOSE_PROFILES": "ai,notifications,search,mcp",
    }
    mcp = render({**base, "COMPOSE_PROFILES": "mcp"})["services"]["mcp"]
    assert mcp["ports"][0]["host_ip"] == "127.0.0.1"
    assert mcp["environment"] == {
        "DEVFEED_MCP_ACCESS_TOKEN_TTL_SECONDS": "900",
        "DEVFEED_MCP_API_URL": "http://api:8000",
        "DEVFEED_MCP_USER_API_URL": "",
        "DEVFEED_MCP_PUBLIC_URL": "",
        "DEVFEED_MCP_OAUTH_ISSUER_URL": "",
        "DEVFEED_MCP_WEB_URL": "",
        "DEVFEED_MCP_REDIS_URL": "",
        "DEVFEED_MCP_ALLOWED_HOSTS": '["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]',
        "DEVFEED_MCP_ALLOWED_ORIGINS": "[]",
    }
    assert set(mcp["networks"]) == {"default", "data"}
    assert set(mcp["depends_on"]) == {"api"}
    assert mcp["image"].endswith("/mcp:master")
    mcp_options = {
        "DEVFEED_MCP_ACCESS_TOKEN_TTL_SECONDS": "600",
        "DEVFEED_MCP_USER_API_URL": "http://user-api:8002",
        "DEVFEED_MCP_PUBLIC_URL": "https://agents.example.test/mcp",
        "DEVFEED_MCP_OAUTH_ISSUER_URL": "https://agents.example.test",
        "DEVFEED_MCP_WEB_URL": "https://reader.example.test",
        "DEVFEED_MCP_REDIS_URL": "redis://redis:6379/1",
        "DEVFEED_MCP_ALLOWED_HOSTS": '["agents.example.test"]',
        "DEVFEED_MCP_ALLOWED_ORIGINS": '["https://reader.example.test"]',
    }
    for build in (False, True):
        account = render({**registry_fixtures, **mcp_options}, build=build)["services"]
        assert account["mcp"]["environment"] == {**mcp["environment"], **mcp_options}
        assert (
            account["web"]["environment"]["DEVFEED_MCP_PUBLIC_URL"]
            == (mcp_options["DEVFEED_MCP_PUBLIC_URL"])
        )
        assert account["web"]["environment"]["DEVFEED_MCP_INTERNAL_URL"] == ""
        check_private_mcp_routing(registry_fixtures, mcp_options, build=build)
        assert (
            account["user-api"]["environment"]["DEVFEED_USER_MCP_RESOURCE_URL"]
            == (mcp_options["DEVFEED_MCP_PUBLIC_URL"])
        )
        assert (
            account["user-api"]["environment"]["DEVFEED_USER_MCP_ISSUER_URL"]
            == (mcp_options["DEVFEED_MCP_OAUTH_ISSUER_URL"])
        )
        upstream = render(registry_fixtures, build=build)
        assert render({**registry_fixtures, "DEVFEED_IMAGE_REGISTRY": ""}, build=build) == upstream
        proxied = render(
            {**registry_fixtures, "DEVFEED_IMAGE_REGISTRY": "registry.home"}, build=build
        )
        for name, service in upstream["services"].items():
            image = service["image"]
            registry = image.partition("/")[0]
            if registry == "devfeed":
                expected = image  # Local build tags never become remote pull targets.
            elif registry == "ghcr.io":
                expected = "registry.home/" + image
            elif registry in {"typesense", "clickhouse"}:
                expected = "registry.home/docker.io/" + image
            else:
                expected = "registry.home/docker.io/library/" + image
            actual = proxied["services"][name]
            assert actual["image"] == expected, name
            if "build" in service:
                for argument in service["build"].get("args", {}):
                    if argument == "DEVFEED_PACKAGE_ARGS":
                        assert (
                            actual["build"]["args"][argument] == service["build"]["args"][argument]
                        )
                    else:
                        assert service["build"]["args"][argument] == ""
                        assert actual["build"]["args"][argument] == (
                            "registry.home/docker.io/library/"
                            if argument == "DOCKERHUB_PREFIX"
                            else "registry.home/"
                        )
                assert actual["build"].get("cache_from", []) == [
                    ref.replace("ref=ghcr.io/", "ref=registry.home/ghcr.io/")
                    for ref in service["build"].get("cache_from", [])
                ]
    overridden = render(
        {
            **base,
            "DEVFEED_IMAGE_REGISTRY": "registry.home",
            "DEVFEED_WEB_IMAGE": "custom.test/web:tag",
        }
    )
    assert overridden["services"]["web"]["image"] == "custom.test/web:tag"
    default = render(base)
    services = default["services"]
    for name in ("web", "user-api"):
        assert services[name]["environment"]["DEVFEED_X_PIXEL_ENABLED"] == "false"
    for key in ("X_PIXEL_TOKEN", "X_SIGNUP_EVENT_ID"):
        assert services["user-api"]["environment"][key] == ""
    for name in ("api", "admin", "web"):
        assert services[name]["ports"][0]["host_ip"] == "0.0.0.0"
    assert services["partner"]["ports"][0]["published"] == "3002"
    assert "data" not in services["partner"]["networks"]
    assert not services["partner-api"].get("ports")
    assert (
        services["partner"]["environment"]["DEVFEED_PARTNER_API_URL"] == "http://partner-api:8004"
    )
    partner_services = render(
        {
            **base,
            "DEVFEED_PARTNER_OIDC_CLIENT_ID": "partner-client",
            "DEVFEED_PARTNER_OIDC_CLIENT_SECRET": "partner-secret",
        }
    )["services"]
    assert (
        partner_services["partner-api"]["environment"]["DEVFEED_OIDC_CLIENT_ID"] == "partner-client"
    )
    assert (
        partner_services["partner-api"]["environment"]["DEVFEED_OIDC_CLIENT_SECRET"]
        == "partner-secret"
    )
    assert "DEVFEED_OIDC_CLIENT_SECRET" not in partner_services["partner"]["environment"]
    storage_secrets = {
        "DEVFEED_IMAGE_STORAGE_ACCESS_KEY": "fixture-image-access-key",
        "DEVFEED_IMAGE_STORAGE_SECRET_KEY": "fixture-image-secret-key",
    }
    for build in (False, True):
        upload_services = render({**registry_fixtures, **storage_secrets}, build=build)["services"]
        for name, service in upload_services.items():
            environment = service.get("environment", {})
            for key, value in storage_secrets.items():
                recipient = name in {"images-worker", "user-api", "admin-api", "partner-api"}
                assert (key in environment) == recipient, (name, key)
                if recipient:
                    assert environment[key] == value, (name, key)
    credential_key = "DEVFEED_PARTNER_CREDENTIAL_KEY"
    for build in (False, True):
        connector_services = render(
            {**registry_fixtures, credential_key: "fixture-credential-encryption-key"}, build=build
        )["services"]
        for name, service in connector_services.items():
            environment = service.get("environment", {})
            if name in {
                "admin-api",
                "partner-api",
                "high-worker",
                "default-worker",
                "low-worker",
            }:
                assert environment[credential_key] == "fixture-credential-encryption-key", name
            else:
                assert credential_key not in environment, name
    placement_options = {
        "DEVFEED_PARTNER_CALLER_KEY": "fixture-caller-" * 3,
        "DEVFEED_PARTNER_CALLER_IP_HEADER": "cf-connecting-ip",
        "DEVFEED_PARTNER_TRACKING_KEY": "fixture-tracking-" * 3,
    }
    placement_recipients = {
        "DEVFEED_PARTNER_CALLER_KEY": {"api", "web"},
        "DEVFEED_PARTNER_CALLER_IP_HEADER": {"web"},
        "DEVFEED_PARTNER_TRACKING_KEY": {"api", "user-api"},
    }
    for build in (False, True):
        placement_services = render({**registry_fixtures, **placement_options}, build=build)[
            "services"
        ]
        for name, service in placement_services.items():
            environment = service.get("environment", {})
            for key, recipients in placement_recipients.items():
                assert (key in environment) == (name in recipients), (name, key)
                if name in recipients:
                    assert environment[key] == placement_options[key], (name, key)
    assert services["admin"]["ports"][0]["published"] == "3001"
    assert services["web"]["ports"][0]["published"] == "3000"
    assert "data" not in services["web"]["networks"]
    assert services["web"]["depends_on"]["api"]["condition"] == "service_healthy"
    for name in ("admin", "admin-api"):
        assert services[name]["environment"]["DEVFEED_ADMIN_BASE_URL"] == "http://localhost:3001"
    for name in ("postgres", "redis", "clickhouse", "admin-api"):
        assert not services[name].get("ports")
    clickhouse = services["clickhouse"]
    assert set(clickhouse["networks"]) == {"data"}
    assert clickhouse["environment"]["CLICKHOUSE_PASSWORD"] == base["POSTGRES_PASSWORD"]
    assert clickhouse["cpus"] == 2
    assert int(clickhouse["mem_limit"]) == 2 * 1024**3
    assert {volume["source"] for volume in clickhouse["volumes"] if volume["type"] == "volume"} == {
        "clickhouse-data",
        "clickhouse-logs",
    }
    assert {volume["target"] for volume in clickhouse["volumes"] if volume["type"] == "bind"} == {
        "/etc/clickhouse-server/config.d/devfeed-resources.xml",
        "/etc/clickhouse-server/users.d/devfeed-analytics.xml",
    }
    assert all(
        volume.get("read_only") for volume in clickhouse["volumes"] if volume["type"] == "bind"
    )
    assert clickhouse["environment"]["DEVFEED_CLICKHOUSE_SERVER_MAX_SELECTS"] == "3"
    assert clickhouse["environment"]["DEVFEED_CLICKHOUSE_SERVER_MAX_INSERTS"] == "2"
    assert services["analytics-importer"]["environment"]["DEVFEED_CLICKHOUSE_USERNAME"] == (
        "devfeed_ingest"
    )
    assert services["admin-api"]["environment"]["DEVFEED_CLICKHOUSE_USERNAME"] == "devfeed_admin"
    assert services["admin-api"]["environment"]["DEVFEED_CLICKHOUSE_PASSWORD"] == (
        "fixture-only-admin-password"
    )
    assert services["analytics-importer"]["cpus"] == Decimal("0.5")
    assert int(services["analytics-importer"]["mem_limit"]) == 512 * 1024**2
    for name, service in services.items():
        assert ("DEVFEED_CLICKHOUSE_PASSWORD" in service.get("environment", {})) == (
            name
            in {
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
        ), name
    assert "clickhouse" not in services["admin-api"]["depends_on"]
    assert "clickhouse" not in services["api"]["depends_on"]
    assert services["migrate"]["depends_on"]["clickhouse"]["condition"] == "service_healthy"
    assert services["migrate"]["environment"]["DEVFEED_CLICKHOUSE_USERNAME"] == "devfeed_ingest"
    assert services["migrate"]["command"][-1] == (
        "devfeed db upgrade && exec devfeed analytics upgrade"
    )
    assert services["analytics-importer"]["depends_on"]["clickhouse"]["condition"] == (
        "service_healthy"
    )
    management = services["ch-ui"]
    assert management["ports"][0]["host_ip"] == "0.0.0.0"
    assert management["ports"][0]["published"] == "4000"
    assert set(management["networks"]) == {"default", "data"}
    assert set(management["depends_on"]) == {"clickhouse"}
    assert management["environment"]["CLICKHOUSE_URL"] == "http://clickhouse:8123"
    assert "CLICKHOUSE_PASSWORD" not in management["environment"]
    check_ch_ui_runtime(management)
    assert int(management["mem_limit"]) == 512 * 1024**2
    for name, service in services.items():
        assert ("APP_SECRET_KEY" in service.get("environment", {})) == (name == "ch-ui")
        assert "ch-ui" not in service.get("depends_on", {})
    assert default["networks"]["data"]["internal"]
    assert services["admin-api"]["environment"].get("DEVFEED_OIDC_ISSUER_URL") is None
    assert services["api"]["environment"].get("DEVFEED_CORS_ORIGINS") is None
    assert services["user-api"]["environment"]["DEVFEED_USER_MCP_RESOURCE_URL"] == ""
    assert services["user-api"]["environment"]["DEVFEED_USER_MCP_ISSUER_URL"] == ""
    assert "chimely" in services and "chimely-db-init" in services
    assert "codex-server" not in services
    priorities = render(
        {**base, "DEVFEED_HIGH_WORKER_REPLICAS": "8", "DEVFEED_DEFAULT_WORKER_REPLICAS": "2"},
        build=True,
    )["services"]
    for queue, replicas in (("high", 8), ("default", 2), ("low", 1)):
        service = priorities[f"{queue}-worker"]
        assert service["image"] == f"devfeed/{queue}-worker:local"
        assert service["build"] == priorities["api"]["build"]
        assert service["environment"]["DEVFEED_WORKER_QUEUE"] == queue
        assert service["scale"] == replicas
        assert service["healthcheck"]["test"] == ["CMD", "devfeed-worker-health"]
        assert {v["source"] for v in service["volumes"] if v["type"] == "volume"} == {
            "chimely-worker-credentials",
            "codex-socket",
        }
    images = priorities["images-worker"]
    assert images["image"] == "devfeed/images-worker:local"
    assert images["build"]["args"]["DEVFEED_PACKAGE_ARGS"] == "--package devfeed-images-worker"
    assert images["command"] == ["devfeed-images-worker"]
    assert "DEVFEED_CHIMELY_ADMIN_API_KEY" not in images["environment"]
    assert not images.get("volumes")
    search = render(
        {
            **base,
            "COMPOSE_PROFILES": "search",
            "DEVFEED_SEARCH_ENABLED": "true",
            "DEVFEED_SEARCH_ADMIN_KEY": "test-index-key",
            "DEVFEED_SEARCH_QUERY_KEY": "test-query-key",
            "DEVFEED_PARTNER_TRACKING_KEY": "disposable-partner-tracking-key-32-bytes",
        },
        build=True,
    )["services"]
    assert (
        search["api"]["environment"]["DEVFEED_PARTNER_TRACKING_KEY"]
        == "disposable-partner-tracking-key-32-bytes"
    )
    assert (
        search["user-api"]["environment"]["DEVFEED_PARTNER_TRACKING_KEY"]
        == "disposable-partner-tracking-key-32-bytes"
    )
    assert all(
        "DEVFEED_PARTNER_TRACKING_KEY" not in service.get("environment", {})
        for name, service in search.items()
        if name not in {"api", "user-api"}
    )
    assert search["search-indexer"]["build"] == search["api"]["build"]
    assert search["search-setup"]["build"] == search["api"]["build"]
    assert search["search-indexer"]["image"] == "devfeed/search-indexer:local"
    assert search["search-setup"]["image"] == "devfeed/search-setup:local"
    assert search["search-indexer"]["command"] == ["devfeed-search-indexer"]
    assert search["search-setup"]["command"] == ["devfeed", "search", "setup"]
    assert "DEVFEED_SEARCH_ADMIN_KEY" not in search["api"]["environment"]
    assert search["api"]["environment"]["DEVFEED_SEARCH_QUERY_KEY"] == "test-query-key"
    assert search["search-indexer"]["environment"]["DEVFEED_SEARCH_ADMIN_KEY"] == "test-index-key"
    assert "DEVFEED_SEARCH_QUERY_KEY" not in search["search-indexer"]["environment"]
    assert search["search-setup"]["environment"]["DEVFEED_SEARCH_ADMIN_KEY"] == "test-index-key"
    assert search["search-setup"]["environment"]["DEVFEED_SEARCH_QUERY_KEY"] == "test-query-key"
    assert search["typesense"]["environment"]["TYPESENSE_API_KEY"] == "test-index-key"
    assert search["typesense"]["volumes"][0]["source"] == "search-data"
    assert not search["typesense"].get("ports")
    assert "codex-server" not in services["admin-api"]["depends_on"]
    assert all(v["read_only"] for v in services["admin-api"]["volumes"])
    for name, volume, role in (
        ("high-worker", "chimely-worker-credentials", "worker"),
        ("default-worker", "chimely-worker-credentials", "worker"),
        ("low-worker", "chimely-worker-credentials", "worker"),
        ("admin-api", "chimely-admin-credentials", "admin"),
        ("user-api", "chimely-user-credentials", "user"),
    ):
        assert (
            services[name]["depends_on"]["chimely-provision"]["condition"]
            == "service_completed_successfully"
        )
        assert services[name]["entrypoint"] == ["python", "/opt/devfeed/chimely-consumer.py", role]
        assert {v["source"] for v in services[name]["volumes"] if v["type"] == "volume"} == (
            {volume, "codex-socket"}
            if name in {"admin-api", "high-worker", "default-worker", "low-worker"}
            else {volume}
        )
        assert all(v["read_only"] for v in services[name]["volumes"])
    assert services["user-api"]["command"][:2] == ["uvicorn", "devfeed_user_api.main:app"]
    assert services["chimely-provision"]["depends_on"]["chimely"]["condition"] == "service_healthy"
    assert services["chimely-provision"]["restart"] == "no"
    assert "data" not in services["chimely-provision"]["networks"]
    assert "DEVFEED_DATABASE_URL" not in services["chimely-provision"]["environment"]
    assert not services["chimely"].get("profiles")
    assert "data" in services["chimely"]["networks"]
    assert services["chimely"]["depends_on"]["chimely-db-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["chimely-db-init"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["chimely-db-init"]["restart"] == "no"
    assert services["chimely-db-init"]["volumes"][0]["read_only"]
    assert (
        services["chimely-db-init"]["environment"]["CHIMELY_POSTGRES_PASSWORD"]
        == base["POSTGRES_PASSWORD"]
    )
    assert services["chimely"]["environment"]["DATABASE_URL"] == (
        f"postgres://chimely:{base['POSTGRES_PASSWORD']}@postgres:5432/chimely"
    )

    # Changing the port alone must also change the default callback origin.
    for bind in ("192.0.2.10", "::1"):
        custom = render(
            {
                **base,
                "DEVFEED_BIND_IP": bind,
                "DEVFEED_ADMIN_PORT": "3101",
                "DEVFEED_WEB_PORT": "3100",
            }
        )
        assert custom["services"]["web"]["ports"][0]["published"] == "3100"
        assert custom["services"]["web"]["ports"][0]["host_ip"] == bind
        admin = custom["services"]["admin"]
        assert admin["ports"][0]["host_ip"] == bind
        assert admin["ports"][0]["published"] == "3101"
        assert admin["environment"]["DEVFEED_ADMIN_BASE_URL"] == "http://localhost:3101"

    options = {
        "DEVFEED_DATABASE_URL": "postgresql+psycopg://external@database.example/test",
        "DEVFEED_REDIS_URL": "redis://cache.example:6379/4",
        "DEVFEED_CORS_ORIGINS": '["https://user.example"]',
        "DEVFEED_CACHE_ENABLED": "false",
        "DEVFEED_FEED_MAX_BYTES": "2000000",
        "DEVFEED_ARTICLE_PAGE_MAX_BYTES": "12000000",
        "DEVFEED_SOURCE_PAGE_MAX_BYTES": "14000000",
        "DEVFEED_FEED_USER_AGENT": "Compose contract check/1.0",
        "DEVFEED_SCHEDULER_BATCH_SIZE": "23",
        "DEVFEED_JOB_LOG_MAX_ENTRIES": "200",
        "DEVFEED_AI_ENABLED": "true",
        "DEVFEED_AUTO_APPROVE_TOPICS": "true",
        "DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS": "true",
        "DEVFEED_AUTO_RESEARCH_RELATIONSHIPS": "true",
        "DEVFEED_FULL_AUTOMATION": "true",
        "DEVFEED_TOPIC_CORRECTION_MAX_ATTEMPTS": "2",
        "DEVFEED_AUTO_LINK_TAGS": "false",
        "DEVFEED_RELATIONSHIP_RESEARCH_BATCH_SIZE": "60",
        "DEVFEED_RELATIONSHIP_RESEARCH_MAX_PENDING": "3",
        "DEVFEED_CODEX_APP_SERVER_URL": "wss://ai.example",
        "DEVFEED_CODEX_MODEL": "test-model",
        "DEVFEED_CODEX_AUTH_TOKEN": "test-ai-token",
        "DEVFEED_NOTIFICATIONS_ENABLED": "true",
    }
    auth = {
        "DEVFEED_ADMIN_BASE_URL": "https://admin.example",
        "DEVFEED_ADMIN_COOKIE_SECURE": "true",
        "DEVFEED_OIDC_ISSUER_URL": "https://identity.example",
        "DEVFEED_OIDC_CLIENT_ID": "test-admin-client",
        "DEVFEED_OIDC_CLIENT_SECRET": "test-oidc-secret",
        "DEVFEED_OIDC_SCOPES": '["openid","profile","email"]',
        "DEVFEED_OIDC_ORGANIZATION_ID": "test-org",
    }
    user_auth = {
        "DEVFEED_USER_OIDC_ISSUER_URL": "https://identity.example",
        "DEVFEED_USER_OIDC_CLIENT_ID": "test-user-client",
        "DEVFEED_USER_OIDC_CLIENT_SECRET": "test-user-secret",
        "DEVFEED_USER_OIDC_ORGANIZATION_ID": "test-org",
    }
    shared_providers = {
        "DEVFEED_OIDC_GITHUB_IDP_ID": "test-github-provider",
        "DEVFEED_OIDC_GOOGLE_IDP_ID": "test-google-provider",
    }
    chimely = {
        "DEVFEED_CHIMELY_API_URL": "http://host.docker.internal:8082",
        "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT": "test-admin",
        "DEVFEED_CHIMELY_ADMIN_API_KEY": "test-management-key",
        "DEVFEED_CHIMELY_ADMIN_HMAC_SECRET": "test-inbox-secret",
    }
    for build in (False, True):
        services = render(
            {**base, **options, **auth, **user_auth, **shared_providers, **chimely}, build=build
        )["services"]
        for name in ("migrate", "api", "high-worker", "scheduler", "admin-api"):
            environment = services[name]["environment"]
            assert all(environment[key] == value for key, value in options.items())
            assert "host.docker.internal=host-gateway" in services[name]["extra_hosts"]
            if name != "admin-api":
                assert not any(key.startswith("DEVFEED_OIDC_") for key in environment)
        assert all(services["admin-api"]["environment"][k] == v for k, v in auth.items())
        assert all(services["user-api"]["environment"][k] == v for k, v in user_auth.items())
        assert not services["user-api"].get("ports")
        assert "user-api" not in services["web"]["depends_on"]
        for name, service in services.items():
            environment = service.get("environment", {})
            assert ("DEVFEED_USER_OIDC_CLIENT_SECRET" in environment) == (name == "user-api")
            for key, value in shared_providers.items():
                assert (key in environment) == (name in {"admin-api", "user-api", "partner-api"})
                if key in environment:
                    assert environment[key] == value
            if name == "user-api":
                assert not any(
                    key.startswith(("DEVFEED_OIDC_", "DEVFEED_CODEX_"))
                    for key in environment
                    if key not in shared_providers
                )

        for name, service in services.items():
            environment = service.get("environment", {})
            assert ("DEVFEED_CHIMELY_ADMIN_API_KEY" in environment) == (
                name in {"high-worker", "default-worker", "low-worker", "chimely-provision"}
            )
            assert ("DEVFEED_CHIMELY_ADMIN_HMAC_SECRET" in environment) == (name == "admin-api")
            assert ("DEVFEED_CHIMELY_USER_HMAC_SECRET" in environment) == (name == "user-api")
            assert ("DEVFEED_CHIMELY_USER_API_KEY" in environment) == (
                name in {"high-worker", "default-worker", "low-worker", "chimely-provision"}
            )
        assert (
            services["high-worker"]["environment"]["DEVFEED_CHIMELY_ADMIN_API_KEY"]
            == (chimely["DEVFEED_CHIMELY_ADMIN_API_KEY"])
        )
        assert (
            services["admin-api"]["environment"]["DEVFEED_CHIMELY_ADMIN_HMAC_SECRET"]
            == (chimely["DEVFEED_CHIMELY_ADMIN_HMAC_SECRET"])
        )
        assert services["web"]["environment"] == {
            "DEVFEED_PUBLIC_API_URL": "http://api:8000",
            "DEVFEED_PARTNER_CALLER_KEY": "",
            "DEVFEED_PARTNER_CALLER_IP_HEADER": "",
            "DEVFEED_USER_API_URL": "http://user-api:8002",
            "DEVFEED_USER_BASE_URL": "http://localhost:3000",
            "DEVFEED_ANALYTICS_ENABLED": "false",
            "GOOGLE_ANALYTICS_ID": "G-N4V5CW5C0M",
            "DEVFEED_X_PIXEL_ENABLED": "false",
            "DEVFEED_USER_EXTENSION_IDS": "[]",
            "DEVFEED_USER_ALLOWED_ORIGINS": "[]",
            "DEVFEED_EXTENSION_ANALYTICS_ENABLED": "false",
            "DEVFEED_EXTENSION_GA_API_SECRET": "",
            "DEVFEED_MCP_PUBLIC_URL": "",
            "DEVFEED_MCP_INTERNAL_URL": "",
        }
        extension_ids = '["hliakjocndflpkmfajndigbpngfcekdm"]'
        reader_origins = '["http://192.0.2.10:3000"]'
        extensions = render(
            {
                **base,
                "DEVFEED_USER_EXTENSION_IDS": extension_ids,
                "DEVFEED_USER_ALLOWED_ORIGINS": reader_origins,
                "DEVFEED_EXTENSION_ANALYTICS_ENABLED": "true",
                "DEVFEED_EXTENSION_GA_API_SECRET": "fixture-only-secret",
            },
            build=build,
        )["services"]
        for name, service in extensions.items():
            environment = service.get("environment", {})
            assert ("DEVFEED_USER_EXTENSION_IDS" in environment) == (name in {"web", "user-api"})
            assert ("DEVFEED_USER_ALLOWED_ORIGINS" in environment) == (name in {"web", "user-api"})
            assert ("DEVFEED_EXTENSION_GA_API_SECRET" in environment) == (name == "web")
        for name in ("web", "user-api"):
            assert extensions[name]["environment"]["DEVFEED_USER_EXTENSION_IDS"] == extension_ids
            assert extensions[name]["environment"]["DEVFEED_USER_ALLOWED_ORIGINS"] == reader_origins
        extension_web = extensions["web"]["environment"]
        assert extension_web["DEVFEED_EXTENSION_ANALYTICS_ENABLED"] == "true"
        assert extension_web["DEVFEED_EXTENSION_GA_API_SECRET"] == "fixture-only-secret"
        assert set(services["admin"]["environment"]) == {
            "DEVFEED_ADMIN_API_URL",
            "DEVFEED_ADMIN_BASE_URL",
        }
        analytics = render(
            {**base, "DEVFEED_ANALYTICS_ENABLED": "true", "GOOGLE_ANALYTICS_ID": "G-TEST123"},
            build=build,
        )["services"]["web"]["environment"]
        assert analytics["DEVFEED_ANALYTICS_ENABLED"] == "true"
        assert analytics["GOOGLE_ANALYTICS_ID"] == "G-TEST123"
        x_options = {
            "X_PIXEL_TOKEN": "fixture-only-x-secret",
            "X_SIGNUP_EVENT_ID": "tw-pc5f8-testevent",
        }
        for enabled in ("false", "true"):
            x_services = render(
                {**base, **x_options, "DEVFEED_X_PIXEL_ENABLED": enabled}, build=build
            )["services"]
            for name, service in x_services.items():
                environment = service.get("environment", {})
                assert ("DEVFEED_X_PIXEL_ENABLED" in environment) == (name in {"web", "user-api"})
                if "DEVFEED_X_PIXEL_ENABLED" in environment:
                    assert environment["DEVFEED_X_PIXEL_ENABLED"] == enabled
                for key, value in x_options.items():
                    assert (key in environment) == (name == "user-api")
                    if key in environment:
                        assert environment[key] == value
    bundled = render(
        {
            **base,
            "COMPOSE_PROFILES": "notifications,ai",
            "CHIMELY_POSTGRES_PASSWORD": "b" * 64,
            "CHIMELY_ADMIN_EMAIL": "admin@example.test",
            "CHIMELY_ADMIN_PASSWORD": "test-bootstrap-password",
            "DEVFEED_AI_ENABLED": "true",
            "DEVFEED_CODEX_APP_SERVER_URL": "unix:///run/codex/app-server.sock",
            "DEVFEED_CODEX_MODEL": "operator-selected",
        },
        build=True,
    )["services"]
    scaled = render(
        {
            **base,
            "COMPOSE_PROFILES": "ai",
            "DEVFEED_HIGH_WORKER_REPLICAS": "10",
            "DEVFEED_DEFAULT_WORKER_REPLICAS": "3",
            "DEVFEED_LOW_WORKER_REPLICAS": "2",
        },
        build=True,
    )["services"]
    assert scaled["high-worker"]["scale"] == 10
    assert scaled["default-worker"]["scale"] == 3
    assert scaled["low-worker"]["scale"] == 2
    backend_services = (
        "migrate",
        "api",
        "high-worker",
        "default-worker",
        "low-worker",
        "scheduler",
    )
    assert len({bundled[name]["image"] for name in backend_services}) == len(backend_services)
    assert all(
        bundled[name]["build"] == bundled["high-worker"]["build"] for name in backend_services
    )
    for queue in ("high", "default", "low"):
        service = bundled[f"{queue}-worker"]
        assert service["environment"]["DEVFEED_AI_ENABLED"] == "true"
        assert service["environment"]["DEVFEED_WORKER_QUEUE"] == queue
        command = " ".join(service["command"])
        assert "uuid.uuid4().hex" in command
        assert "--name" in command and "--queue" in command
        assert "exec devfeed-worker --queue" in command
        assert 'runtime_marker("worker-name")' in command
        assert service["healthcheck"]["test"] == ["CMD", "devfeed-worker-health"]
        assert not service.get("ports") and not service.get("network_mode")
        assert "data" in service["networks"]
    assert bundled["high-worker"]["command"] == bundled["default-worker"]["command"]
    assert not bundled["codex-server"].get("ports")
    assert {v["source"] for v in bundled["codex-server"]["volumes"]} == {
        "codex-auth",
        "codex-socket",
    }
    assert "data" not in bundled["codex-server"]["networks"]
    assert not bundled["codex-server"].get("environment")
    for name in (
        "api",
        "high-worker",
        "default-worker",
        "low-worker",
        "scheduler",
        "admin-api",
        "user-api",
        "admin",
        "web",
        "codex-server",
    ):
        rules = bundled[name]["develop"]["watch"]
        assert rules and all(rule["action"] == "rebuild" for rule in rules)
    assert not bundled["migrate"].get("develop")
    admin_paths = {Path(rule["path"]).resolve() for rule in bundled["admin"]["develop"]["watch"]}
    assert ROOT / "apps/admin" in admin_paths
    assert ROOT / "package-lock.json" in admin_paths
    for name in ("api", "high-worker", "default-worker", "low-worker", "scheduler", "admin-api"):
        paths = {Path(rule["path"]).resolve() for rule in bundled[name]["develop"]["watch"]}
        assert ROOT / "packages/core" in paths and ROOT / "uv.lock" in paths
        assert ROOT / "packages/http" in paths
    assert bundled["chimely"]["ports"][0]["host_ip"] == "0.0.0.0"
    assert not bundled["chimely-db-init"].get("ports")
    assert bundled["chimely"]["environment"]["DATABASE_URL"] == (
        f"postgres://chimely:{'b' * 64}@postgres:5432/chimely"
    )
    for name, service in bundled.items():
        environment = service.get("environment", {})
        assert ("CHIMELY_ADMIN_PASSWORD" in environment) == (
            name in {"chimely", "chimely-provision"}
        )
        assert ("DATABASE_URL" in environment) == (name == "chimely")
    encode = runpy.run_path(str(ROOT / "scripts/compose_dev.py"))["env_line"]
    password = "a$literal\\'\"tail"
    quoted = encode("CHIMELY_ADMIN_PASSWORD", password).partition("=")[2]
    escaped = render(
        {
            **base,
            "COMPOSE_PROFILES": "notifications",
            "CHIMELY_ADMIN_PASSWORD": quoted,
        }
    )["services"]["chimely"]["environment"]["CHIMELY_ADMIN_PASSWORD"]
    assert escaped.replace("$$", "$") == password
    print("Compose checks passed: IP binding, profiles, local images and credential isolation")


if __name__ == "__main__":
    check()
