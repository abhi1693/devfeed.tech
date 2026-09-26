"""Disposable CI services; callers never supply a database or Redis target."""

import os
import subprocess
import time
from contextlib import contextmanager


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=180).strip()


@contextmanager
def disposable_services():
    containers = []
    try:
        postgres = docker(
            "run",
            "-d",
            "--rm",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_USER=ci",
            "-e",
            "POSTGRES_PASSWORD=ci",
            "-e",
            "POSTGRES_DB=devfeed_test",
            "postgres:18-alpine",
        )
        containers.append(postgres)
        redis = docker("run", "-d", "--rm", "-p", "127.0.0.1::6379", "redis:8-alpine")
        containers.append(redis)
        for _ in range(60):
            ready = (
                subprocess.run(
                    ["docker", "exec", postgres, "pg_isready", "-U", "ci", "-d", "devfeed_test"],
                    capture_output=True,
                    timeout=10,
                ).returncode
                == 0
            )
            if ready and docker("exec", redis, "redis-cli", "ping") == "PONG":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable CI services did not start")
        pg_port = docker("port", postgres, "5432/tcp").rsplit(":", 1)[1]
        redis_port = docker("port", redis, "6379/tcp").rsplit(":", 1)[1]
        env = {key: value for key, value in os.environ.items() if not key.startswith("DEVFEED_")}
        env.update(
            DEVFEED_DATABASE_URL=f"postgresql+psycopg://ci:ci@127.0.0.1:{pg_port}/devfeed_test",
            DEVFEED_REDIS_URL=f"redis://127.0.0.1:{redis_port}/15",
            DEVFEED_AI_ENABLED="false",
            DEVFEED_NOTIFICATIONS_ENABLED="false",
            DEVFEED_METRICS_ENABLED="false",
            DEVFEED_CACHE_ENABLED="false",
            DEVFEED_IMAGE_STORAGE_ENABLED="false",
        )
        yield env
    finally:
        for container in reversed(containers):
            subprocess.run(
                ["docker", "rm", "-f", container], check=True, capture_output=True, timeout=30
            )
