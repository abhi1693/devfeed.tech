"""Render isolated Compose fixtures to check networking and credential routing."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def render(values: dict[str, str], *, build: bool = False) -> dict:
    # Never read the developer's .env or inherited application credentials.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DEVFEED_", "COMPOSE_", "POSTGRES_"))
    }
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / ".env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        command = ["docker", "compose", "--env-file", str(env_file), "-f", "compose.yaml"]
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
    return json.loads(result.stdout)


def check() -> None:
    base = {"POSTGRES_PASSWORD": "a" * 64}
    default = render(base)
    services = default["services"]
    for name in ("api", "admin"):
        assert services[name]["ports"][0]["host_ip"] == "0.0.0.0"
    for name in ("postgres", "redis", "admin-api"):
        assert not services[name].get("ports")
    assert default["networks"]["data"]["internal"]
    assert services["admin-api"]["environment"].get("DEVFEED_OIDC_ISSUER_URL") is None
    assert services["api"]["environment"].get("DEVFEED_CORS_ORIGINS") is None

    # Changing the port alone must also change the default callback origin.
    for bind in ("192.0.2.10", "::1"):
        custom = render({**base, "DEVFEED_BIND_IP": bind, "DEVFEED_ADMIN_PORT": "3101"})
        admin = custom["services"]["admin"]
        assert admin["ports"][0]["host_ip"] == bind
        assert admin["ports"][0]["published"] == "3101"
        assert admin["environment"]["DEVFEED_ADMIN_BASE_URL"] == "http://localhost:3101"

    options = {
        "DEVFEED_DATABASE_URL": "postgresql+psycopg://external@database.example/test",
        "DEVFEED_REDIS_URL": "redis://cache.example:6379/4",
        "DEVFEED_CORS_ORIGINS": '["https://reader.example"]',
        "DEVFEED_CACHE_ENABLED": "false",
        "DEVFEED_FEED_MAX_BYTES": "2000000",
        "DEVFEED_FEED_USER_AGENT": "Compose contract check/1.0",
        "DEVFEED_SCHEDULER_BATCH_SIZE": "23",
        "DEVFEED_JOB_LOG_MAX_ENTRIES": "200",
        "DEVFEED_AI_ENABLED": "true",
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
    chimely = {
        "DEVFEED_CHIMELY_API_URL": "http://host.docker.internal:8082",
        "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT": "test-admin",
        "DEVFEED_CHIMELY_ADMIN_API_KEY": "test-management-key",
        "DEVFEED_CHIMELY_ADMIN_HMAC_SECRET": "test-inbox-secret",
    }
    for build in (False, True):
        services = render({**base, **options, **auth, **chimely}, build=build)["services"]
        for name in ("migrate", "api", "worker", "scheduler", "admin-api"):
            environment = services[name]["environment"]
            assert all(environment[key] == value for key, value in options.items())
            assert "host.docker.internal=host-gateway" in services[name]["extra_hosts"]
            if name != "admin-api":
                assert not any(key.startswith("DEVFEED_OIDC_") for key in environment)
        assert all(services["admin-api"]["environment"][k] == v for k, v in auth.items())
        for name, service in services.items():
            environment = service.get("environment", {})
            assert ("DEVFEED_CHIMELY_ADMIN_API_KEY" in environment) == (name == "worker")
            assert ("DEVFEED_CHIMELY_ADMIN_HMAC_SECRET" in environment) == (name == "admin-api")
        assert (
            services["worker"]["environment"]["DEVFEED_CHIMELY_ADMIN_API_KEY"]
            == (chimely["DEVFEED_CHIMELY_ADMIN_API_KEY"])
        )
        assert (
            services["admin-api"]["environment"]["DEVFEED_CHIMELY_ADMIN_HMAC_SECRET"]
            == (chimely["DEVFEED_CHIMELY_ADMIN_HMAC_SECRET"])
        )
        assert set(services["admin"]["environment"]) == {
            "DEVFEED_ADMIN_API_URL",
            "DEVFEED_ADMIN_BASE_URL",
        }
    print("Compose checks passed: defaults, IP binding, ports, overrides and credential isolation")


if __name__ == "__main__":
    check()
