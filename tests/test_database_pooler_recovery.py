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
from typing import Annotated

import pytest
from devfeed_core.config import Settings, get_settings
from devfeed_core.db import create_database_engine
from devfeed_core.version import SCHEMA_REVISION
from devfeed_http.dependencies import session_dependency
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration
POOLER_IMAGE = (
    "ghcr.io/cloudnative-pg/pgbouncer:1.25.2@sha256:"
    "15c9765980546b8660ff105c06ad6ef9d2001fb9c5b31400021a1952cbe49930"
)


def docker(*args):
    # Pull progress is written to stderr on a cold runner. Keep it separate from
    # machine-readable stdout (container IDs, network IDs and inspect JSON).
    return subprocess.run(
        ["docker", *args], text=True, capture_output=True, check=True, timeout=60
    ).stdout.strip()


@pytest.fixture
def pooler(tmp_path, monkeypatch, request):
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
max_client_conn = 60
query_wait_timeout = 2
admin_users = ci
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
            database_pool_enabled=getattr(request, "param", False),
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
        yield SimpleNamespace(
            engines=engines,
            network=network,
            proxy=proxy,
            ip=ip,
            config=config,
            pg=pg,
            directory=tmp_path,
            containers=containers,
        )
    finally:
        for engine in engines:
            engine.dispose()
        for container in reversed(containers):
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)
        get_settings.cache_clear()


@pytest.mark.parametrize("pooler", [True, False], indirect=True, ids=["queue-pool", "null-pool"])
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


def test_null_pool_shares_five_server_slots_across_api_and_worker_engines(pooler):
    """More replicas than server slots must make progress and release idle clients."""
    import psycopg

    engines = [create_database_engine(pooler.config) for _ in range(12)]
    pooler.engines.extend(engines)
    clients = []
    for index in range(6):
        service = ("devfeed_api", "devfeed_admin_api", "devfeed_user_api")[index % 3]
        app = importlib.import_module(service + ".main").create_app()
        dependencies = importlib.import_module(service + ".dependencies")
        factory = sessionmaker(engines[index])

        def bind(factory):
            return session_dependency(lambda: factory)

        app.dependency_overrides[dependencies.get_session] = bind(factory)

        # Exercise each real service's middleware and session cleanup around a
        # deterministic DB operation, without coupling load tests to domain data.
        def probe(session: Annotated[Session, Depends(dependencies.get_session, scope="function")]):
            return {"value": session.scalar(text("SELECT 1 FROM pg_sleep(0.01)"))}

        app.add_api_route("/pool-load", probe, methods=["GET"])
        clients.append(TestClient(app))
    barrier = threading.Barrier(len(engines))

    def workload(index):
        engine = engines[index]
        barrier.wait(timeout=5)
        durations = []
        for iteration in range(200):
            started = time.monotonic()
            if index < len(clients):
                response = clients[index].get("/pool-load")
                assert response.status_code == 200
                assert response.json() == {"value": 1}
            else:
                with engine.begin() as connection:
                    assert connection.scalar(text("SELECT 1 FROM pg_sleep(0.01)")) == 1
            durations.append(time.monotonic() - started)
            if iteration == 100:
                # Replace process-local connection state during ongoing traffic.
                engine.dispose()
        return durations

    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=len(engines)) as executor:
            results = list(executor.map(workload, range(len(engines))))
    finally:
        for client in clients:
            client.close()
    # QueuePool engines would leave five idle clients pinning all server slots.
    # Even after every replica is idle, fresh work must still get a slot.
    with engines[-1].connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
    with psycopg.connect(
        host=pooler.ip, port=6432, user="ci", dbname="pgbouncer", autocommit=True
    ) as admin:
        pools = admin.execute("SHOW POOLS").fetchall()
        columns = [column.name for column in admin.execute("SHOW POOLS").description]
        rows = [dict(zip(columns, row, strict=True)) for row in pools]
        app_pool = next(row for row in rows if row["database"] == "recovery_test")
        assert app_pool["cl_waiting"] == 0
        assert app_pool["cl_active"] == 0
        assert app_pool["sv_active"] == 0
        assert app_pool["sv_idle"] <= 5
    durations = sorted(value for result in results for value in result)
    print(
        f"\n12 NullPool API/worker engines, {len(durations)} transactions, "
        f"5 server slots: {time.monotonic() - started:.2f}s; "
        f"p95={durations[int(len(durations) * 0.95)]:.3f}s; "
        f"max={max(durations):.3f}s; no retained clients"
    )


def test_pooler_queue_wait_is_bounded_and_recovers_without_replaying_writes(pooler):
    """NullPool removes local waiting; PgBouncer must bound server-slot waiting."""
    engine = create_database_engine(pooler.config)
    pooler.engines.append(engine)
    held = []
    try:
        for _ in range(5):
            connection = engine.connect()
            held.append(connection)
            connection.execute(text("SELECT 1"))
        started = time.monotonic()
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text("INSERT INTO recovery_probe VALUES (2)"))
        elapsed = time.monotonic() - started
        assert 1 <= elapsed < 5
    finally:
        for connection in held:
            connection.close()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM recovery_probe")) == 0
    print(f"\nPgBouncer exhaustion failed in {elapsed:.2f}s and recovered after slot release")


def test_lock_contention_is_cancelled_and_releases_the_server_slot(pooler):
    engine = create_database_engine(pooler.config)
    pooler.engines.append(engine)
    with engine.begin() as owner:
        owner.execute(text("LOCK TABLE recovery_probe IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(DBAPIError), engine.begin() as blocked:
            blocked.execute(text("SET LOCAL statement_timeout = '150ms'"))
            blocked.execute(text("SELECT * FROM recovery_probe"))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM recovery_probe")) == 0


def test_loss_of_one_pooler_preserves_other_instance_and_aborts_inflight_write(pooler):
    healthy_proxy = docker(
        "run",
        "-d",
        "--rm",
        "--network",
        pooler.network,
        "--label",
        "codex.task=devfeed-db-recovery",
        "--mount",
        f"type=bind,src={pooler.directory},dst=/etc/pgbouncer,readonly",
        POOLER_IMAGE,
    )
    pooler.containers.append(healthy_proxy)
    healthy_ip = next(
        iter(
            json.loads(docker("inspect", healthy_proxy))[0]["NetworkSettings"]["Networks"].values()
        )
    )["IPAddress"]
    config = pooler.config.model_copy(
        update={"database_url": f"postgresql+psycopg://ci:ci@{healthy_ip}:6432/recovery_test"}
    )
    healthy = create_database_engine(config)
    pooler.engines.append(healthy)
    for _ in range(50):
        try:
            with healthy.connect() as connection:
                assert connection.scalar(text("SELECT 1")) == 1
            break
        except DBAPIError:
            time.sleep(0.1)
    else:
        pytest.fail("Second disposable pooler did not start")
    started = threading.Event()

    def interrupted_write():
        with pooler.engines[0].begin() as connection:
            connection.execute(text("INSERT INTO recovery_probe VALUES (3)"))
            started.set()
            connection.execute(text("SELECT pg_sleep(25)"))

    with ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(interrupted_write)
        assert started.wait(5)
        docker("network", "disconnect", "-f", pooler.network, pooler.proxy)
        lost_at = time.monotonic()
        # Represents new connections routed to the remaining ready endpoint.
        # The test does not pretend to exercise Kubernetes endpoint propagation.
        for _ in range(100):
            with healthy.begin() as connection:
                assert connection.scalar(text("SELECT 1 FROM pg_sleep(0.01)")) == 1
        with pytest.raises(DBAPIError):
            active.result(timeout=18)
        assert time.monotonic() - lost_at < 20
        docker("network", "connect", "--ip", pooler.ip, pooler.network, pooler.proxy)
    with pooler.engines[0].connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM recovery_probe")) == 0
    print(
        "\nOne pooler lost: 100 transactions through healthy instance, aborted write not replayed"
    )
