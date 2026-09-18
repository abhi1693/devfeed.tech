"""Own a disposable real HTTP API + PgBouncer + PostgreSQL + Redis Locust run.

Run with uv run --locked --group loadtest python scripts/load-test.py.
Only resources returned by this invocation are cleaned up; no existing DB is touched.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POOLER = (
    "ghcr.io/cloudnative-pg/pgbouncer:1.25.2@sha256:"
    "15c9765980546b8660ff105c06ad6ef9d2001fb9c5b31400021a1952cbe49930"
)


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=90).strip()


def port(container, target):
    return docker("port", container, f"{target}/tcp").split(":")[-1]


def ready(check, description):
    for _ in range(60):
        try:
            if check():
                return
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{description} did not become ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--spawn-rate", type=float, default=2)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--cache", choices=("on", "off"), default="off")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/loadtest")
    args = parser.parse_args()
    if not (1 <= args.users <= 200 and 0 < args.spawn_rate <= 100 and 10 <= args.seconds <= 3600):
        parser.error("Use 1..200 users, spawn rate >0..100, and duration 10..3600 seconds")
    if not 100 <= args.rows <= 10000:
        parser.error("Use 100..10000 rows")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    containers, processes = [], []
    network = None
    # Ignore local app/Locust credentials and automation settings. The API runs
    # outside the checkout so Settings cannot discover the developer's .env file.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DEVFEED_", "LOCUST_"))}
    env.update(
        DEVFEED_DATABASE_POOL_ENABLED="false",
        DEVFEED_CACHE_ENABLED=str(args.cache == "on").lower(),
        DEVFEED_API_MAX_CONCURRENT_REQUESTS="16",
        DEVFEED_LOG_LEVEL="WARNING",
        DEVFEED_METRICS_ENABLED="false",
        DEVFEED_NOTIFICATIONS_ENABLED="false",
        DEVFEED_AI_ENABLED="false",
        DEVFEED_FULL_AUTOMATION="false",
    )
    with tempfile.TemporaryDirectory(prefix="devfeed-locust-") as directory:
        temp = Path(directory)
        try:
            network = docker("network", "create", "devfeed-locust-" + uuid.uuid4().hex[:12])

            def start(*arguments):
                container = docker("run", "-d", "--rm", "--network", network, *arguments)
                containers.append(container)
                return container

            pg = start(
                "--network-alias",
                "db",
                "-p",
                "127.0.0.1::5432",
                "-e",
                "POSTGRES_USER=load",
                "-e",
                "POSTGRES_PASSWORD=load",
                "-e",
                "POSTGRES_DB=devfeed_load_test",
                "postgres:18-alpine",
            )
            redis = start("-p", "127.0.0.1::6379", "redis:8-alpine")
            ready(
                lambda: (
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            pg,
                            "pg_isready",
                            "-U",
                            "load",
                            "-d",
                            "devfeed_load_test",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    ).returncode
                    == 0
                ),
                "PostgreSQL",
            )
            ready(lambda: docker("exec", redis, "redis-cli", "ping") == "PONG", "Redis")
            env["DEVFEED_REDIS_URL"] = f"redis://127.0.0.1:{port(redis, 6379)}/15"
            env["DEVFEED_DATABASE_URL"] = (
                f"postgresql+psycopg://load:load@127.0.0.1:{port(pg, 5432)}/devfeed_load_test"
            )
            with (args.output / "setup.log").open("w") as log:
                for command in (
                    [
                        sys.executable,
                        "-m",
                        "alembic",
                        "-c",
                        str(ROOT / "alembic.ini"),
                        "upgrade",
                        "head",
                    ],
                    [sys.executable, str(ROOT / "loadtests/seed.py"), str(args.rows)],
                ):
                    subprocess.run(
                        command, env=env, cwd=temp, check=True, stdout=log, stderr=log, timeout=120
                    )
            (temp / "pgbouncer.ini").write_text("""[databases]
devfeed_load_test = host=db port=5432 dbname=devfeed_load_test user=load password=load
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = trust
auth_file = /etc/pgbouncer/users.txt
admin_users = load
pool_mode = session
default_pool_size = 5
max_client_conn = 60
query_wait_timeout = 2
pidfile = /tmp/pgbouncer.pid
""")
            (temp / "users.txt").write_text('"load" "load"\n')
            temp.chmod(0o755)
            for file in temp.iterdir():
                file.chmod(0o644)
            proxy = start(
                "-p",
                "127.0.0.1::6432",
                "--mount",
                f"type=bind,src={temp},dst=/etc/pgbouncer,readonly",
                POOLER,
            )
            proxy_port = port(proxy, 6432)
            env["DEVFEED_DATABASE_URL"] = (
                f"postgresql+psycopg://load:load@127.0.0.1:{proxy_port}/devfeed_load_test"
            )
            with socket.socket() as listener, (args.output / "api.log").open("w") as log:
                listener.bind(("127.0.0.1", 0))
                listener.listen(128)
                api_port = listener.getsockname()[1]
                api = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "devfeed_api.main:app",
                        "--fd",
                        str(listener.fileno()),
                        "--no-access-log",
                        "--no-proxy-headers",
                    ],
                    env=env,
                    cwd=temp,
                    pass_fds=(listener.fileno(),),
                    stdout=log,
                    stderr=log,
                )
                processes.append(api)
                host = f"http://127.0.0.1:{api_port}"
                ready(
                    lambda: urllib.request.urlopen(host + "/health/ready", timeout=3).status == 200,
                    "API",
                )
                metadata = {
                    "started_at": datetime.now(UTC).isoformat(),
                    "locust_version": version("locust"),
                    "worktree_dirty": bool(
                        subprocess.check_output(
                            ["git", "status", "--porcelain"], cwd=ROOT, text=True
                        ).strip()
                    ),
                    "users": args.users,
                    "spawn_rate": args.spawn_rate,
                    "seconds": args.seconds,
                    "rows": args.rows,
                    "cache": args.cache,
                    "pool": "NullPool",
                    "pgbouncer_mode": "session",
                    "server_slots": 5,
                    "api_instances": 1,
                    "admission": 16,
                    "host": host,
                    "commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                    ).strip(),
                }
                (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "locust",
                        "-f",
                        str(ROOT / "loadtests/locustfile.py"),
                        "--headless",
                        "--host",
                        host,
                        "--users",
                        str(args.users),
                        "--spawn-rate",
                        str(args.spawn_rate),
                        "--run-time",
                        str(args.seconds),
                        "--stop-timeout",
                        "10",
                        "--reset-stats",
                        "--only-summary",
                        "--csv",
                        str(args.output / "reader"),
                        "--csv-full-history",
                        "--html",
                        str(args.output / "reader.html"),
                    ],
                    env=env,
                    cwd=temp,
                    timeout=args.seconds + 60,
                )
                # Record server/client ownership after requests finish, while the
                # API remains alive: retained local pools would pin idle clients.
                import psycopg
                from psycopg.rows import dict_row

                with psycopg.connect(
                    host="127.0.0.1",
                    port=proxy_port,
                    user="load",
                    dbname="pgbouncer",
                    autocommit=True,
                    row_factory=dict_row,
                ) as connection:
                    pools = connection.execute("SHOW POOLS").fetchall()
                (args.output / "pooler-after.json").write_text(json.dumps(pools, indent=2) + "\n")
                retained = any(
                    row["database"] == "devfeed_load_test"
                    and any(row[key] for key in ("cl_active", "cl_waiting", "sv_active"))
                    for row in pools
                )
                if retained:
                    print("Connections remain attached after load stopped", file=sys.stderr)
                print(f"Locust artifacts: {args.output}")
                return result.returncode or int(retained)
        finally:
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for container in reversed(containers):
                subprocess.run(
                    ["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, timeout=30
                )
            if network:
                subprocess.run(
                    ["docker", "network", "rm", network], stdout=subprocess.DEVNULL, timeout=30
                )


if __name__ == "__main__":
    raise SystemExit(main())
