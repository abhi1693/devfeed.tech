"""Opt-in destructive faults affect only Docker resources created by this test.

Run DEVFEED_TEST_DATABASE_FAILURES=1 uv run pytest -q -s tests/test_database_pooler_recovery.py
Requires Linux Docker networking; no application credentials or existing DB are used.
"""

import importlib
import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import create_database_engine
from devfeed_core.version import SCHEMA_REVISION
from devfeed_http.dependencies import session_dependency
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration
POOLER_IMAGE = (
    "ghcr.io/cloudnative-pg/pgbouncer:1.25.2@sha256:"
    "15c9765980546b8660ff105c06ad6ef9d2001fb9c5b31400021a1952cbe49930"
)


def docker(*args):
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.STDOUT, timeout=60
    ).strip()


@pytest.fixture
def pooler(tmp_path, monkeypatch):
    if os.environ.get("DEVFEED_TEST_DATABASE_FAILURES") != "1":
        pytest.skip(
            "Opt in with DEVFEED_TEST_DATABASE_FAILURES=1; creates disposable Docker resources"
        )
    name = "devfeed-db-recovery-" + uuid.uuid4().hex[:12]
    existing = json.loads(docker("network", "inspect", *docker("network", "ls", "-q").split()))
    allocated = [
        ipaddress.ip_network(config["Subnet"])
        for item in existing
        for config in item["IPAM"].get("Config", []) or []
        if config.get("Subnet")
    ]
    subnet = next(
        candidate
        for candidate in ipaddress.ip_network("10.254.0.0/16").subnets(new_prefix=24)
        if not any(candidate.overlaps(used) for used in allocated if used.version == 4)
    )
    network = docker(
        "network",
        "create",
        "--subnet",
        str(subnet),
        "--label",
        "codex.task=devfeed-db-recovery",
        name,
    )
    containers = []
    engines = []
    try:
        pg = docker(
            "run",
            "-d",
            "--rm",
            "--network",
            network,
            "--network-alias",
            "db",
            "--label",
            "codex.task=devfeed-db-recovery",
            "-e",
            "POSTGRES_USER=ci",
            "-e",
            "POSTGRES_PASSWORD=ci",
            "-e",
            "POSTGRES_DB=recovery_test",
            "postgres:18-alpine",
        )
        containers.append(pg)
        for _ in range(60):
            if (
                subprocess.run(
                    ["docker", "exec", pg, "pg_isready", "-U", "ci", "-d", "recovery_test"],
                    capture_output=True,
                ).returncode
                == 0
            ):
                break
            time.sleep(0.2)
        else:
            pytest.fail("Disposable PostgreSQL did not start")
        tmp_path.chmod(0o755)
        (tmp_path / "pgbouncer.ini").write_text("""[databases]
recovery_test = host=db port=5432 dbname=recovery_test user=ci password=ci
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = trust
auth_file = /etc/pgbouncer/users.txt
pool_mode = session
default_pool_size = 5
max_client_conn = 20
pidfile = /tmp/pgbouncer.pid
""")
        (tmp_path / "users.txt").write_text('"ci" "ci"\n')
        for path in tmp_path.iterdir():
            path.chmod(0o644)
        proxy = docker(
            "run",
            "-d",
            "--rm",
            "--network",
            network,
            "--label",
            "codex.task=devfeed-db-recovery",
            "--mount",
            f"type=bind,src={tmp_path},dst=/etc/pgbouncer,readonly",
            POOLER_IMAGE,
        )
        containers.append(proxy)
        ip = next(
            iter(json.loads(docker("inspect", proxy))[0]["NetworkSettings"]["Networks"].values())
        )["IPAddress"]
        # Direct bridge IP deliberately avoids Docker's TCP forwarding proxy:
        # that proxy would ACK packets and mask the actual broken network path.
        url = f"postgresql+psycopg://ci:ci@{ip}:6432/recovery_test"
        monkeypatch.setenv("DEVFEED_DATABASE_URL", url)
        monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://redis.invalid/15")
        get_settings.cache_clear()
        config = Settings(
            database_url=url,
            redis_url="redis://redis.invalid/15",
            database_pool_size=1,
            database_max_overflow=0,
        )
        engines = [create_database_engine(config) for _ in range(2)]
        for _ in range(50):
            try:
                with engines[0].begin() as connection:
                    connection.execute(
                        text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32))")
                    )
                    connection.execute(text("DELETE FROM alembic_version"))
                    connection.execute(
                        text("INSERT INTO alembic_version VALUES (:revision)"),
                        {"revision": SCHEMA_REVISION},
                    )
                    connection.execute(text("CREATE TABLE recovery_probe (id integer)"))
                break
            except DBAPIError:
                time.sleep(0.1)
        else:
            pytest.fail("Disposable PgBouncer did not start")
        yield SimpleNamespace(engines=engines, network=network, proxy=proxy, ip=ip)
    finally:
        for engine in engines:
            engine.dispose()
        for container in reversed(containers):
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)
        get_settings.cache_clear()


def test_two_single_connection_api_pools_recover_after_pooler_network_loss(pooler, monkeypatch):
    main = importlib.import_module("devfeed_user_api.main")
    dependencies = importlib.import_module("devfeed_user_api.dependencies")
    monkeypatch.setattr(main, "get_redis", lambda: SimpleNamespace(ping=lambda: True))
    clients = []
    for engine in pooler.engines:
        app = main.create_app()
        factory = sessionmaker(engine)

        # Bind each factory before moving to the next simulated replica.
        def bind(factory):
            return session_dependency(lambda: factory)

        app.dependency_overrides[dependencies.get_session] = bind(factory)
        clients.append(TestClient(app))
        assert clients[-1].get("/health/ready").status_code == 200
        with (
            engine.connect() as connection,
            socket.fromfd(
                connection.connection.driver_connection.fileno(), socket.AF_INET, socket.SOCK_STREAM
            ) as sock,
        ):
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) == 5
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) == 2
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT) == 2
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == 8000
    started = threading.Event()

    def interrupted_transaction():
        with pooler.engines[0].begin() as connection:
            connection.execute(text("INSERT INTO recovery_probe VALUES (1)"))
            started.set()
            connection.execute(text("SELECT pg_sleep(25)"))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            active = executor.submit(interrupted_transaction)
            assert started.wait(5)
            docker("network", "disconnect", "-f", pooler.network, pooler.proxy)
            lost_at = time.monotonic()
            idle = executor.submit(clients[1].get, "/health/ready")
            # The busy pool fails promptly without waiting for its blocked owner.
            before = time.monotonic()
            assert clients[0].get("/health/ready").status_code == 503
            assert time.monotonic() - before < 4
            for client in clients:
                assert client.get("/health/live").status_code == 200
            with pytest.raises(DBAPIError) as error:
                active.result(timeout=18)
            assert error.value.connection_invalidated
            assert idle.result(timeout=5).status_code == 503
            detection = time.monotonic() - lost_at
            assert detection < 20
            docker("network", "connect", "--ip", pooler.ip, pooler.network, pooler.proxy)
            restored_at = time.monotonic()
            # Same engines and app instances: no dispose, restart or write replay.
            for client in clients:
                assert client.get("/health/ready").status_code == 200
            for engine in pooler.engines:
                with engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM recovery_probe")) == 0
                    assert connection.scalar(text("SHOW statement_timeout")) == "30s"
            print(
                f"\nTCP loss detected in {detection:.2f}s; "
                f"both pools ready again in {time.monotonic() - restored_at:.2f}s"
            )
    finally:
        for client in clients:
            client.close()
