"""Owned PostgreSQL/Redis faults; no caller-supplied service targets."""

import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy.engine import make_url

POSTGRES_IMAGE = (
    "postgres:18-alpine@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873"
)
REDIS_IMAGE = (
    "redis:8-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0"
)
TOXIPROXY_IMAGE = (
    "ghcr.io/shopify/toxiproxy:2.12.0@sha256:"
    "9378ed52a28bc50edc1350f936f518f31fa95f0d15917d6eb40b8e376d1a214e"
)
OWNER_LABEL = "devfeed.failure-recovery"


def docker(*args):
    return subprocess.run(
        ["docker", *args], text=True, capture_output=True, check=True, timeout=180
    ).stdout.strip()


def published_port(container, port):
    return docker("port", container, f"{port}/tcp").rsplit(":", 1)[1]


class FaultProxy:
    """Control only the proxy container created by this suite's owner."""

    def __init__(self, url, container, owner):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path:
            raise ValueError("Fault control must be on loopback")
        info = json.loads(docker("inspect", container))[0]
        if not owner or info["Config"]["Labels"].get(OWNER_LABEL) != owner:
            raise ValueError("Fault container is not owned by this suite")
        if url != f"http://127.0.0.1:{published_port(container, 8474)}":
            raise ValueError("Fault control does not match its owned container")
        self.container = container
        self.client = httpx.Client(base_url=url, timeout=3, trust_env=False)

    def verify_targets(self, database_url, redis_url):
        database = make_url(database_url)
        redis = urlsplit(redis_url)
        if (
            database.drivername != "postgresql+psycopg"
            or database.host != "127.0.0.1"
            or database.database != "recovery_test"
            or str(database.port) != published_port(self.container, 15432)
            or redis.scheme != "redis"
            or redis.hostname != "127.0.0.1"
            or redis.path != "/15"
            or str(redis.port) != published_port(self.container, 16379)
        ):
            raise ValueError("Test data targets must match the owned proxy ports")
        for name, upstream in [("postgres", "postgres:5432"), ("redis", "redis:6379")]:
            if self.request("GET", f"/proxies/{name}").json()["upstream"] != upstream:
                raise ValueError("Fault upstream must be an owned service")

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def enabled(self, name, value):
        if name not in {"postgres", "redis"}:
            raise ValueError("Unknown fault target")
        self.request("POST", f"/proxies/{name}", json={"enabled": value})

    @contextmanager
    def fault(self, name, kind, *, milliseconds=700):
        if name not in {"postgres", "redis"} or kind not in {"disconnect", "latency", "stall"}:
            raise ValueError("Unknown fault")
        try:
            if kind == "disconnect":
                self.enabled(name, False)
            else:
                self.request(
                    "POST",
                    f"/proxies/{name}/toxics",
                    json={
                        "name": "recovery",
                        "type": "latency" if kind == "latency" else "timeout",
                        "stream": "downstream",
                        "toxicity": 1,
                        "attributes": {"latency": milliseconds, "jitter": 0}
                        if kind == "latency"
                        else {"timeout": milliseconds},
                    },
                )
            yield
        finally:
            self.restore()

    def restore(self):
        self.request("POST", "/reset")

    def close(self):
        self.client.close()


@contextmanager
def disposable_failure_services(directory: Path):
    owner = "devfeed-recovery-" + uuid.uuid4().hex[:12]
    network = None
    containers = {}
    control = None
    directory.mkdir(parents=True, exist_ok=True)
    try:
        network = docker("network", "create", "--label", f"{OWNER_LABEL}={owner}", owner)

        def start(role, image, memory, *args):
            container = docker(
                "run",
                "-d",
                "--network",
                network,
                "--network-alias",
                role,
                "--label",
                f"{OWNER_LABEL}={owner}",
                "--memory",
                memory,
                "--cpus",
                "1",
                "--pids-limit",
                "256",
                *args,
                image,
            )
            containers[role] = container
            return container

        postgres = start(
            "postgres",
            POSTGRES_IMAGE,
            "512m",
            "--tmpfs",
            "/var/lib/postgresql:rw,size=256m",
            "-e",
            "POSTGRES_USER=ci",
            "-e",
            "POSTGRES_PASSWORD=ci",
            "-e",
            "POSTGRES_DB=recovery_test",
        )
        redis = start("redis", REDIS_IMAGE, "128m", "--tmpfs", "/data:rw,size=64m")
        proxy = start(
            "proxy",
            TOXIPROXY_IMAGE,
            "128m",
            "-p",
            "127.0.0.1::8474",
            "-p",
            "127.0.0.1::15432",
            "-p",
            "127.0.0.1::16379",
        )
        url = f"http://127.0.0.1:{published_port(proxy, 8474)}"
        control = FaultProxy(url, proxy, owner)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                ready = (
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            postgres,
                            "pg_isready",
                            "-U",
                            "ci",
                            "-d",
                            "recovery_test",
                        ],
                        capture_output=True,
                        timeout=5,
                    ).returncode
                    == 0
                )
                if ready and docker("exec", redis, "redis-cli", "ping") == "PONG":
                    control.request("GET", "/version")
                    break
            except (httpx.HTTPError, subprocess.CalledProcessError):
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("Recovery services did not become ready")
        for name, listen, upstream in [
            ("postgres", "15432", "postgres:5432"),
            ("redis", "16379", "redis:6379"),
        ]:
            control.request(
                "POST",
                "/proxies",
                json={
                    "name": name,
                    "listen": f"0.0.0.0:{listen}",
                    "upstream": upstream,
                    "enabled": True,
                },
            )
        database_url = (
            f"postgresql+psycopg://ci:ci@127.0.0.1:{published_port(proxy, 15432)}/recovery_test"
        )
        redis_url = f"redis://127.0.0.1:{published_port(proxy, 16379)}/15"
        # Drop inherited application settings; pytest disables .env loading too.
        env = {key: value for key, value in os.environ.items() if not key.startswith("DEVFEED_")}
        env.update(
            DEVFEED_TEST_DATABASE_URL=database_url,
            DEVFEED_TEST_REDIS_URL=redis_url,
            DEVFEED_DATABASE_URL=database_url,
            DEVFEED_REDIS_URL=redis_url,
            DEVFEED_TEST_FAILURE_RECOVERY="1",
            DEVFEED_TEST_FAILURE_OWNER=owner,
            DEVFEED_TEST_TOXIPROXY_URL=url,
            DEVFEED_TEST_TOXIPROXY_CONTAINER=proxy,
            DEVFEED_METRICS_ENABLED="false",
            DEVFEED_OTLP_ENDPOINT="",
        )
        (directory / "services.json").write_text(
            json.dumps(
                {
                    "owner": owner,
                    "network": network,
                    "containers": containers,
                    "images": [POSTGRES_IMAGE, REDIS_IMAGE, TOXIPROXY_IMAGE],
                },
                indent=2,
            )
            + "\n"
        )
        yield env
    finally:
        # Capture diagnostics before deleting only the resources we created.
        if control:
            with suppress(httpx.HTTPError, OSError):
                (directory / "proxies.json").write_text(control.request("GET", "/proxies").text)
            control.close()
        failures = []
        for role, container in reversed(list(containers.items())):
            with suppress(OSError, subprocess.TimeoutExpired):
                logs = subprocess.run(
                    ["docker", "logs", container], capture_output=True, text=True, timeout=10
                )
                (directory / f"{role}.log").write_text(logs.stdout + logs.stderr)
            try:
                result = subprocess.run(
                    ["docker", "rm", "-fv", container], capture_output=True, timeout=30
                )
                if result.returncode:
                    failures.append(container)
            except (OSError, subprocess.TimeoutExpired):
                failures.append(container)
        if network:
            try:
                result = subprocess.run(
                    ["docker", "network", "rm", network], capture_output=True, timeout=30
                )
                if result.returncode:
                    failures.append(network)
            except (OSError, subprocess.TimeoutExpired):
                failures.append(network)
        if failures:
            raise RuntimeError("Failed to clean up owned recovery resources")
