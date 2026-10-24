"""Own a disposable real HTTP API + PgBouncer + PostgreSQL + Redis Locust run.

Run with uv run --locked --group loadtest python scripts/load-test.py.
Only resources returned by this invocation are cleaned up; no existing DB is touched.
"""

import argparse
import json
import math
import os
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POOLER = (
    "ghcr.io/cloudnative-pg/pgbouncer:1.25.2@sha256:"
    "15c9765980546b8660ff105c06ad6ef9d2001fb9c5b31400021a1952cbe49930"
)
STATEMENT_COUNTERS = (
    "calls",
    "total_exec_time",
    "rows",
    "shared_blks_hit",
    "shared_blks_read",
    "shared_blks_dirtied",
    "shared_blks_written",
    "temp_blks_read",
    "temp_blks_written",
)
DIAGNOSTIC_STATEMENTS = (
    "pg_stat_statements",
    "pg_stat_activity",
    "pg_stat_database",
    "pg_stat_clear_snapshot",
)


def process_statistics(pid, proc_root=Path("/proc")):
    # The process name can contain spaces and parentheses. Fields after its
    # closing parenthesis begin at state (field 3).
    fields = (proc_root / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return {
        "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
        "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
    }


def optional_process_statistics(pid):
    try:
        return process_statistics(pid) if pid is not None else None
    except FileNotFoundError:
        # Locust can finish between poll() and reading its /proc entry.
        return None


def statement_delta(before, after):
    previous = {row["queryid"]: row for row in before}
    result = []
    for row in after:
        initial = previous.get(row["queryid"], {})
        change = {key: row[key] - initial.get(key, 0) for key in STATEMENT_COUNTERS}
        if any(value < 0 for value in change.values()):
            raise ValueError("PostgreSQL statement counters reset during measurement")
        if change["calls"]:
            result.append({"queryid": row["queryid"], "query": row["query"], **change})
    return sorted(result, key=lambda row: row["total_exec_time"], reverse=True)


def runtime_snapshot(database, pooler, pg, api_pid, load_pid, phase):
    database.execute("/* performance diagnostics */ SELECT pg_stat_clear_snapshot()")
    queries = database.execute(
        """/* performance diagnostics */
        SELECT queryid::text, query, calls, total_exec_time, rows,
               shared_blks_hit, shared_blks_read, shared_blks_dirtied, shared_blks_written,
               temp_blks_read, temp_blks_written
        FROM pg_stat_statements
        WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND toplevel AND query NOT LIKE '%performance diagnostics%'
        """
    ).fetchall()
    # PostgreSQL can retain normalized SQL without its leading diagnostic marker.
    queries = [
        row
        for row in queries
        if not any(name in row["query"].lower() for name in DIAGNOSTIC_STATEMENTS)
    ]
    activity = database.execute(
        """/* performance diagnostics */
        SELECT state, wait_event_type, wait_event, count(*) AS connections
        FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()
        GROUP BY state, wait_event_type, wait_event ORDER BY state, wait_event_type, wait_event
        """
    ).fetchall()
    database_stats = database.execute(
        "/* performance diagnostics */ SELECT * FROM pg_stat_database "
        "WHERE datname = current_database()"
    ).fetchone()
    cpu = {
        key: int(value)
        for key, value in (
            line.split()
            for line in docker("exec", pg, "cat", "/sys/fs/cgroup/cpu.stat").splitlines()
        )
    }
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "monotonic": time.monotonic(),
        "phase": phase,
        "api_process": process_statistics(api_pid),
        "load_process": optional_process_statistics(load_pid),
        "postgres_cgroup": cpu,
        "database": database_stats,
        "activity": activity,
        # Repeated snapshots cross Psycopg's automatic preparation threshold.
        # PgBouncer's admin console only accepts the simple query protocol.
        "pooler": pooler.execute("SHOW POOLS", prepare=False).fetchall(),
        "queries": queries,
    }


def measurement_diagnostics(samples):
    measured = [sample for sample in samples if sample["phase"]["phase"] == "measurement"]
    finished = [sample for sample in samples if sample["phase"]["phase"] == "finished"]
    if not measured or not finished:
        raise ValueError("Load test did not record a completed measurement phase")
    start, end = measured[0], finished[0]
    return {
        "observed_start_at": start["recorded_at"],
        "observed_end_at": end["recorded_at"],
        "start_observation_lag_seconds": start["monotonic"] - start["phase"]["started_monotonic"],
        "end_observation_lag_seconds": end["monotonic"] - end["phase"]["finished_monotonic"],
        "api_cpu_seconds": end["api_process"]["cpu_seconds"] - start["api_process"]["cpu_seconds"],
        "postgres_cpu_seconds": (
            end["postgres_cgroup"]["usage_usec"] - start["postgres_cgroup"]["usage_usec"]
        )
        / 1_000_000,
        "queries": statement_delta(start["queries"], end["queries"]),
    }


def stop_load_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_measured_load(command, *, env, cwd, timeout, phase_file, output, capture):
    """Retain diagnostics even when traffic, telemetry or the watchdog fails.

    Phase boundaries are observed by polling, not included in Locust latency
    timings. Their observed lag is retained instead of claiming exact CPU or SQL
    boundaries. Periodic pool/activity samples use a separate direct DB connection.
    """
    samples, process = [], None
    report = {"sample_interval_seconds": 5, "phase_poll_seconds": 0.1, "samples": samples}
    phase = {"phase": "starting"}
    previous_phase, next_sample = None, 0
    try:
        process = subprocess.Popen(command, env=env, cwd=cwd)
        deadline = time.monotonic() + timeout
        while True:
            if phase_file.exists():
                phase = json.loads(phase_file.read_text())
            moment = time.monotonic()
            stopped = process.poll() is not None
            if phase["phase"] != previous_phase or moment >= next_sample or stopped:
                samples.append(capture(None if stopped else process.pid, phase))
                previous_phase, next_sample = phase["phase"], moment + 5
            if stopped:
                report["exit_code"] = process.returncode
                break
            if moment >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(0.1)
        report["measurement"] = measurement_diagnostics(samples)
        return process.returncode
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        stop_load_process(process)
        output.write_text(json.dumps(report, indent=2, default=str) + "\n")


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=90).strip()


def port(container, target):
    return docker("port", container, f"{target}/tcp").split(":")[-1]


@contextmanager
def api_listener():
    with socket.socket() as listener:
        # Uvicorn inherits this socket through --fd. Set the normal HTTP
        # no-delay behavior explicitly rather than relying on the event loop
        # to configure inherited sockets (uvloop 0.23 no longer does).
        listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        yield listener


def ready(check, description):
    for _ in range(60):
        try:
            if check():
                return
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{description} did not become ready")


def report_directory(destination: Path) -> Path:
    # Anchor reports to this invocation, including CI runs outside the candidate checkout.
    root = Path.cwd().resolve() / "reports"
    resolved = destination.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Output must remain within the current directory's reports folder")
    return resolved


def statistics_report(output: Path):
    # Linux O_NOFOLLOW rejects existing symlinks, while regular files support repeat runs.
    descriptor = os.open(
        output / "database-statistics.json",
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    return os.fdopen(descriptor, "w", encoding="utf-8")


def prepare_pooler(directory, database):
    row = database.execute(
        "SELECT rolpassword AS scram_verifier FROM pg_authid WHERE rolname = current_user"
    ).fetchone()
    verifier = row["scram_verifier"] if row else None
    if not isinstance(verifier, str) or not verifier.startswith("SCRAM-SHA-256$"):
        raise ValueError("The disposable PostgreSQL user must have a stored SCRAM verifier")
    # SCRAM passthrough requires the server's exact verifier and no forced backend
    # user: https://www.pgbouncer.org/config.html#authentication-file-format.
    (directory / "pgbouncer.ini").write_text("""[databases]
devfeed_load_test = host=db port=5432 dbname=devfeed_load_test
[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/users.txt
admin_users = load
pool_mode = session
default_pool_size = 5
max_client_conn = 60
query_wait_timeout = 2
pidfile = /tmp/pgbouncer.pid
""")
    (directory / "users.txt").write_text(f'"load" "{verifier}"\n')


def prepare_database(args, env, cwd, statistics):
    with (args.output / "setup.log").open("w") as log:
        subprocess.run(
            [
                str(args.app_python),
                "-m",
                "alembic",
                "-c",
                str(args.app_root / "alembic.ini"),
                "upgrade",
                "head",
            ],
            env=env,
            cwd=cwd,
            check=True,
            stdout=log,
            stderr=log,
            timeout=120,
        )
        subprocess.run(
            [str(args.app_python), str(ROOT / "loadtests/seed.py"), str(args.rows)],
            env=env,
            cwd=cwd,
            check=True,
            stdout=statistics,
            stderr=log,
            timeout=120,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=ROOT)
    parser.add_argument("--app-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--spawn-rate", type=float, default=2)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--warmup-seconds", type=int, default=15)
    parser.add_argument("--journey-seed", type=int, default=294)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--cache", choices=("on", "off"), default="off")
    parser.add_argument("--output", type=Path, default=Path("reports/loadtest"))
    args = parser.parse_args()
    if not (1 <= args.users <= 200 and 0 < args.spawn_rate <= 100 and 10 <= args.seconds <= 3600):
        parser.error("Use 1..200 users, spawn rate >0..100, and duration 10..3600 seconds")
    if not 100 <= args.rows <= 10000:
        parser.error("Use 100..10000 rows")
    if not 1 <= args.warmup_seconds <= 300:
        parser.error("Use 1..300 warmup seconds")
    args.app_root = args.app_root.resolve()
    args.app_python = args.app_python.absolute()
    try:
        args.output = report_directory(args.output)
    except ValueError as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    containers, processes, connections = [], [], []
    password = secrets.token_hex(32)
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
    with (
        statistics_report(args.output) as statistics,
        tempfile.TemporaryDirectory(prefix="devfeed-locust-") as directory,
    ):
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
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=devfeed_load_test",
                "postgres:18-alpine",
                "-c",
                "shared_preload_libraries=pg_stat_statements",
                "-c",
                "pg_stat_statements.track=top",
                "-c",
                "track_io_timing=on",
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
                            "-h",
                            "127.0.0.1",
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
            import psycopg
            from psycopg.rows import dict_row

            database_port = port(pg, 5432)
            diagnostics_database = psycopg.connect(
                host="127.0.0.1",
                port=database_port,
                user="load",
                password=password,
                dbname="devfeed_load_test",
                autocommit=True,
                row_factory=dict_row,
            )
            connections.append(diagnostics_database)
            diagnostics_database.execute("CREATE EXTENSION pg_stat_statements")
            env["DEVFEED_REDIS_URL"] = f"redis://127.0.0.1:{port(redis, 6379)}/15"
            env["DEVFEED_DATABASE_URL"] = (
                f"postgresql+psycopg://load:{password}@127.0.0.1:{database_port}/devfeed_load_test"
            )
            prepare_database(args, env, temp, statistics)
            prepare_pooler(temp, diagnostics_database)
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
                f"postgresql+psycopg://load:{password}@127.0.0.1:{proxy_port}/devfeed_load_test"
            )
            with api_listener() as listener, (args.output / "api.log").open("w") as log:
                api_port = listener.getsockname()[1]
                api = subprocess.Popen(
                    [
                        str(args.app_python),
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
                    "protocol_version": "reader-load-v2",
                    "journey_seed": args.journey_seed,
                    "warmup_seconds": args.warmup_seconds,
                    "started_at": datetime.now(UTC).isoformat(),
                    "locust_version": version("locust"),
                    "runner_name": os.environ.get("RUNNER_NAME", "local"),
                    "architecture": platform.machine(),
                    "logical_cpus": os.cpu_count(),
                    "worktree_dirty": bool(
                        subprocess.check_output(
                            ["git", "status", "--porcelain"], cwd=args.app_root, text=True
                        ).strip()
                    ),
                    "users": args.users,
                    "spawn_rate": args.spawn_rate,
                    "seconds": args.seconds,
                    "rows": args.rows,
                    "cache": args.cache,
                    "requested_pool": "NullPool",
                    "pgbouncer_mode": "session",
                    "server_slots": 5,
                    "api_instances": 1,
                    "requested_admission": 16,
                    "host": host,
                    "commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=args.app_root, text=True
                    ).strip(),
                    "postgres_version": diagnostics_database.execute(
                        "SELECT version() AS version"
                    ).fetchone()["version"],
                    "container_images": {
                        name: docker("inspect", "--format", "{{.Image}}", container)
                        for name, container in (
                            ("postgres", pg),
                            ("redis", redis),
                            ("pooler", proxy),
                        )
                    },
                }
                (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
                pooler = psycopg.connect(
                    host="127.0.0.1",
                    port=proxy_port,
                    user="load",
                    password=password,
                    dbname="pgbouncer",
                    autocommit=True,
                    row_factory=dict_row,
                )
                connections.append(pooler)
                phase_file = args.output / "measurement.json"
                phase_file.unlink(missing_ok=True)
                watchdog = (
                    math.ceil(args.users / args.spawn_rate)
                    + args.warmup_seconds
                    + args.seconds
                    + 120
                )
                result = run_measured_load(
                    [
                        sys.executable,
                        "-m",
                        "locust",
                        "-f",
                        str(ROOT / "loadtests/locustfile.py"),
                        "--headless",
                        "--final-json",
                        str(args.output / "final.json"),
                        "--host",
                        host,
                        "--users",
                        str(args.users),
                        "--spawn-rate",
                        str(args.spawn_rate),
                        "--run-time",
                        str(watchdog),
                        "--measurement-seconds",
                        str(args.seconds),
                        "--warmup-seconds",
                        str(args.warmup_seconds),
                        "--journey-seed",
                        str(args.journey_seed),
                        "--phase-file",
                        str(phase_file),
                        "--stop-timeout",
                        "10",
                        "--only-summary",
                        "--csv",
                        str(args.output / "reader"),
                        "--csv-full-history",
                        "--html",
                        str(args.output / "reader.html"),
                    ],
                    env=env,
                    cwd=temp,
                    timeout=watchdog + 60,
                    phase_file=phase_file,
                    output=args.output / "runtime-diagnostics.json",
                    capture=lambda load_pid, phase: runtime_snapshot(
                        diagnostics_database, pooler, pg, api.pid, load_pid, phase
                    ),
                )
                # Record server/client ownership after requests finish, while the
                # API remains alive: retained local pools would pin idle clients.
                pools = pooler.execute("SHOW POOLS", prepare=False).fetchall()
                (args.output / "pooler-after.json").write_text(json.dumps(pools, indent=2) + "\n")
                retained = any(
                    row["database"] == "devfeed_load_test"
                    and any(row[key] for key in ("cl_active", "cl_waiting", "sv_active"))
                    for row in pools
                )
                if retained:
                    print("Connections remain attached after load stopped", file=sys.stderr)
                print(f"Locust artifacts: {args.output}")
                return result or int(retained)
        finally:
            for connection in reversed(connections):
                connection.close()
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
