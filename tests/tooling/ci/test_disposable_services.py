"""CI analytics services stay private, isolated and owned through setup failures."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.tooling.ci.test_ci_gates import load_script

services = load_script("services")


@pytest.fixture
def owned(monkeypatch):
    commands, probes, removed = [], [], []

    def docker(*args):
        commands.append(args)
        if args[0] == "run":
            image = args[-1]
            return (
                "owned-ch"
                if "clickhouse" in image
                else ("owned-pg" if "postgres" in image else "owned-redis")
            )
        if args[0] == "port":
            return {
                "owned-pg": "127.0.0.1:15432",
                "owned-redis": "127.0.0.1:16379",
                "owned-ch": "127.0.0.1:18123",
            }[args[1]]
        assert args == ("exec", "owned-redis", "redis-cli", "ping")
        return "PONG"

    def run(command, **kwargs):
        if command[1:3] == ["rm", "-f"]:
            removed.append(command[-1])
            assert kwargs["check"] is True
        else:
            probes.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(services, "docker", docker)
    monkeypatch.setattr(services.subprocess, "run", run)
    monkeypatch.setattr(services.secrets, "token_hex", lambda size: "owned-analytics-secret")
    monkeypatch.setenv("DEVFEED_CLICKHOUSE_PASSWORD", "inherited-private-value")
    monkeypatch.setenv("DEVFEED_DATABASE_URL", "postgresql://private/production")
    return SimpleNamespace(commands=commands, probes=probes, removed=removed)


@pytest.mark.parametrize("analytics", [False, True])
def test_clickhouse_is_opt_in_bounded_authenticated_and_cleanup_owned(owned, analytics):
    with services.disposable_services(analytics=analytics) as environment:
        assert environment["DEVFEED_DATABASE_URL"].endswith(":15432/devfeed_test")
        assert environment["DEVFEED_REDIS_URL"] == "redis://127.0.0.1:16379/15"
        assert [
            "docker",
            "exec",
            "owned-pg",
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-U",
            "ci",
            "-d",
            "devfeed_test",
        ] in owned.probes
        assert not owned.removed
        if analytics:
            assert environment["DEVFEED_CLICKHOUSE_URL"] == "http://127.0.0.1:18123"
            assert environment["DEVFEED_CLICKHOUSE_PASSWORD"] == "owned-analytics-secret"
            launch = next(
                command
                for command in owned.commands
                if "owned-analytics-secret" in " ".join(command)
            )
            assert "127.0.0.1::8123" in launch
            assert launch[launch.index("--cpus") + 1] == "2"
            assert launch[launch.index("--memory") + 1] == "2g"
            assert "CLICKHOUSE_USER=ci" in launch and "CLICKHOUSE_DB=devfeed_test" in launch
            assert launch[-1] == services.CLICKHOUSE_IMAGE
            assert any("clickhouse-client" in command for command in owned.probes)
        else:
            assert "DEVFEED_CLICKHOUSE_PASSWORD" not in environment
            assert not any("clickhouse" in command[-1] for command in owned.commands)
    assert owned.removed == (
        ["owned-ch", "owned-redis", "owned-pg"] if analytics else ["owned-redis", "owned-pg"]
    )


def test_failed_native_launch_cleans_previously_owned_services(owned, monkeypatch):
    original = services.docker

    def fail(*args):
        if args[0] == "run" and args[-1] == services.CLICKHOUSE_IMAGE:
            raise subprocess.CalledProcessError(1, ["docker", *args])
        return original(*args)

    monkeypatch.setattr(services, "docker", fail)
    with pytest.raises(subprocess.CalledProcessError), services.disposable_services(analytics=True):
        pytest.fail("Failed native setup reached the fuzz tests")
    assert owned.removed == ["owned-redis", "owned-pg"]


def test_health_deadline_cleans_native_services_without_running_tests(owned, monkeypatch):
    def unavailable(command, **kwargs):
        if command[1:3] == ["rm", "-f"]:
            owned.removed.append(command[-1])
        return SimpleNamespace(returncode=1)

    pause = Mock()
    monkeypatch.setattr(services.subprocess, "run", unavailable)
    monkeypatch.setattr(services.time, "sleep", pause)
    with (
        pytest.raises(RuntimeError, match="did not start"),
        services.disposable_services(analytics=True),
    ):
        pytest.fail("Unhealthy services reached the fuzz tests")
    assert pause.call_count == 60
    assert owned.removed == ["owned-ch", "owned-redis", "owned-pg"]


def test_docker_boundary_uses_bounded_argument_execution(monkeypatch):
    execute = Mock(return_value="owned-container\n")
    monkeypatch.setattr(services.subprocess, "check_output", execute)
    assert services.docker("port", "owned-container", "8123/tcp") == "owned-container"
    execute.assert_called_once_with(
        ["docker", "port", "owned-container", "8123/tcp"], text=True, timeout=180
    )
