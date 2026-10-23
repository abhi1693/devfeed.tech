"""Run Fleet's non-Codex commands inside the actual native candidate image.

Only owned containers on an internal Docker network are addressed. No host
DevFeed settings, credentials, production endpoints or private repositories enter
the fixture. The backend proves Alembic; trimmed API images use metadata only.
"""

import json
import platform
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "scripts/ci/fleet_contracts"
CONTRACTS = json.loads((FIXTURES / "commands.json").read_text())["contracts"]
TYPESENSE = (
    "typesense/typesense:30.2@sha256:"
    "610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110"
)


def command(*args, timeout=180, check=True):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"Command failed: {args[0:3]}\n{result.stdout}\n{result.stderr}")
    return result


class Smoke:
    def __init__(self, image, component):
        self.image, self.component = image, component
        self.prefix = "fleet-native-" + uuid.uuid4().hex[:12]
        self.containers = []
        self.volumes = []
        self.network = None
        self.directory = tempfile.TemporaryDirectory(prefix=self.prefix)
        self.files = Path(self.directory.name)
        self.files.chmod(0o755)
        self.env = {
            "DEVFEED_AI_ENABLED": "false",
            "DEVFEED_NOTIFICATIONS_ENABLED": "false",
            "DEVFEED_METRICS_ENABLED": "false",
            "DEVFEED_CACHE_ENABLED": "false",
            "DEVFEED_IMAGE_STORAGE_ENABLED": "false",
            "DEVFEED_LOG_FORMAT": "json",
            "DEVFEED_WORKER_HEALTH_PATH": "/tmp/devfeed-worker-health.json",
        }

    def __enter__(self):
        self.network = self.prefix
        command("docker", "network", "create", "--internal", self.network)
        return self

    def __exit__(self, *exc):
        try:
            for name in reversed(self.containers):
                command("docker", "rm", "-f", name, timeout=30, check=False)
            for volume in reversed(self.volumes):
                command("docker", "volume", "rm", volume, timeout=30)
            if self.network:
                command("docker", "network", "rm", self.network, timeout=30, check=False)
        finally:
            self.directory.cleanup()

    def start(self, args=(), *, env=None, mounts=(), frontend=False):
        name = self.prefix + "-" + str(len(self.containers))
        options = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--hostname",
            self.prefix,
            "--network",
            self.network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000:1000" if frontend else "10001:10001",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,mode=1777",
            "--mount",
            f"type=bind,src={FIXTURES},dst=/fleet,readonly",
        ]
        for key, value in {**self.env, **(env or {})}.items():
            options += ["-e", f"{key}={value}"]
        for mount in mounts:
            options += ["--mount", mount]
        if frontend:
            options += [
                "--tmpfs",
                f"/app/apps/{self.component}/.next/cache:rw,uid=1000,gid=1000,mode=0700",
            ]
        self.containers.append(name)
        command(*options, self.image, *args)
        return name

    def run(self, args, **options):
        name = self.start(args, **options)
        result = command("docker", "wait", name, timeout=120)
        result_logs = command("docker", "logs", name)
        logs = result_logs.stdout + result_logs.stderr
        if result.stdout.strip() != "0":
            raise RuntimeError(f"Native command failed ({self.component}): {logs}")
        return logs

    def execute(self, name, *args, check=True):
        return command("docker", "exec", name, *args, timeout=30, check=check)

    def stop(self, name):
        command("docker", "stop", "-t", "15", name, timeout=25)
        status = command(
            "docker", "inspect", "--format", "{{.State.ExitCode}}", name
        ).stdout.strip()
        if status != "0":
            logs = command("docker", "logs", name).stdout
            raise RuntimeError(f"Native command did not shut down cleanly: {status}\n{logs}")

    def wait_probe(self, name, probe, seconds=45):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.execute(name, *probe, check=False).returncode == 0:
                return
            if (
                command("docker", "inspect", "--format", "{{.State.Running}}", name).stdout.strip()
                != "true"
            ):
                break
            time.sleep(0.5)
        raise RuntimeError(
            f"Native probe failed: {probe}\n" + command("docker", "logs", name).stdout
        )

    def services(self):
        pg = self.prefix + "-pg"
        redis = self.prefix + "-redis"
        for name, image, args in (
            (
                pg,
                "postgres:18-alpine",
                [
                    "-e",
                    "POSTGRES_USER=ci",
                    "-e",
                    "POSTGRES_PASSWORD=ci",
                    "-e",
                    "POSTGRES_DB=fleet_native_test",
                ],
            ),
            (redis, "redis:8-alpine", []),
        ):
            self.containers.append(name)
            command("docker", "run", "-d", "--name", name, "--network", self.network, *args, image)
        self.wait_probe(pg, ["pg_isready", "-U", "ci", "-d", "fleet_native_test"])
        self.wait_probe(redis, ["redis-cli", "-n", "15", "ping"])
        self.redis = redis
        self.env.update(
            DEVFEED_DATABASE_URL=f"postgresql+psycopg://ci:ci@{pg}/fleet_native_test",
            DEVFEED_REDIS_URL=f"redis://{redis}/15",
            DEVFEED_READ_MODEL_DATABASE_URL=f"postgresql+psycopg://ci:ci@{pg}/fleet_native_test",
        )

    def schema(self):
        if self.component == "backend":
            self.run(["python", "-m", "alembic", "upgrade", "0033"])
            self.run(["python", "/fleet/runtime.py", "seed"])
            self.run(CONTRACTS["migrate"]["command"])
            self.run(["python", "/fleet/runtime.py", "retained"])
            print(
                "PASS native packaged Alembic 0033 -> 0034 and Fleet migration hook; "
                "retained article/account"
            )
        else:
            print(self.run(["python", "/fleet/runtime.py", "metadata"]).strip())
        self.run(CONTRACTS["wait-schema"]["command"])

    def ca(self):
        command(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=disposable-native-fleet-ca",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-keyout",
            str(self.files / "fixture.key"),
            "-out",
            str(self.files / "ca.crt"),
        )
        (self.files / "ca.crt").chmod(0o444)
        volume = self.prefix + "-ca"
        command("docker", "volume", "create", volume)
        self.volumes.append(volume)
        prepare = self.prefix + "-ca-prepare"
        self.containers.append(prepare)
        # The kubelet prepares emptyDir ownership before the non-root init runs.
        # Do the equivalent only inside this owned volume, never on host paths.
        command(
            "docker",
            "run",
            "--name",
            prepare,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,src={volume},dst=/bundle",
            "--entrypoint",
            "python",
            self.image,
            "-c",
            "import os; os.chown('/bundle',0,10001); os.chmod('/bundle',0o770)",
        )
        mounts = [
            f"type=bind,src={self.files / 'ca.crt'},dst=/run/codex-ca/ca.crt,readonly",
            f"type=volume,src={volume},dst=/run/ca",
        ]
        self.run(CONTRACTS["ca"]["command"], mounts=mounts)
        self.bundle_mount = f"type=volume,src={volume},dst=/run/ca,readonly"
        self.env["SSL_CERT_FILE"] = "/run/ca/ca-bundle.crt"
        self.ca_mounts = [self.bundle_mount]
        self.run(
            ["python", "/fleet/runtime.py", "ca"],
            mounts=[mounts[0], self.bundle_mount],
        )
        print("PASS native CA init, root:10001 mode0770 volume, uid10001 write/read-only SSL load")

    def inbox(self, role):
        directory = self.files / role
        directory.mkdir(exist_ok=True)
        directory.chmod(0o755)
        values = {
            "DEVFEED_CHIMELY_API_URL": "http://chimely:8080",
            "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT": "devfeed-admin",
            "DEVFEED_CHIMELY_USER_ENVIRONMENT": "devfeed-user",
        }
        suffix = "API_KEY" if role == "worker" else "HMAC_SECRET"
        for audience in ("ADMIN", "USER") if role == "worker" else (role.upper(),):
            values[f"DEVFEED_CHIMELY_{audience}_{suffix}"] = "disposable-fake-credential-" * 2
        path = directory / "credentials.json"
        path.write_text(json.dumps(values))
        path.chmod(0o444)
        mounts = [
            f"type=bind,src={directory},dst=/run/devfeed-chimely,readonly",
            f"type=bind,src={ROOT / 'infra/chimely/consumer.py'},"
            "dst=/opt/devfeed/consumer.py,readonly",
        ]
        env = {
            "DEVFEED_NOTIFICATIONS_ENABLED": "true",
            "DEVFEED_CHIMELY_API_URL": "http://chimely:8080",
            "DEVFEED_CHIMELY_ADMIN_ENVIRONMENT": "devfeed-admin",
            "DEVFEED_CHIMELY_USER_ENVIRONMENT": "devfeed-user",
        }
        self.run(CONTRACTS["wait-inbox"]["command"], mounts=mounts)
        return mounts, env

    def api(self):
        key = "api" if self.component == "backend" else self.component
        port = {"api": 8000, "admin-api": 8001, "user-api": 8002, "partner-api": 8004}[key]
        mounts, env = list(getattr(self, "ca_mounts", [])), {}
        if key in {"admin-api", "user-api"}:
            inbox_mounts, env = self.inbox("admin" if key == "admin-api" else "user")
            mounts += inbox_mounts
        name = self.start(CONTRACTS[key]["command"], mounts=mounts, env=env)
        self.wait_probe(
            name, ["python", "/fleet/runtime.py", "ready", f"http://127.0.0.1:{port}/health/ready"]
        )
        self.execute(name, "python", "/fleet/runtime.py", "permissions")
        # A working listener must still refuse a schema that this release cannot read.
        self.execute(name, "python", "/fleet/runtime.py", "set-revision", "0033")
        self.execute(
            name,
            "python",
            "/fleet/runtime.py",
            "reject-schema",
            f"http://127.0.0.1:{port}/health/ready",
        )
        self.execute(name, "python", "/fleet/runtime.py", "restore-revision")
        self.stop(name)
        print(f"PASS native Fleet {key} entrypoint, readiness and schema rejection")

    def workers(self):
        queues = (
            [
                "ingestion",
                "analysis",
                "article-analysis",
                "topic-analysis",
                "research-verification",
                "relationships",
                "source-analysis",
                "source-enrichment",
                "solver",
                "notifications",
            ]
            if self.component == "backend"
            else [
                {
                    "article-enrichment-worker": "article-enrichment",
                    "images-worker": "images",
                    "source-discovery-worker": "source-discovery",
                }[self.component]
            ]
        )
        for queue in queues:
            self.execute(self.redis, "redis-cli", "-n", "15", "flushdb")
            env = {"DEVFEED_WORKER_QUEUE": queue}
            mounts = list(getattr(self, "ca_mounts", []))
            key = "worker" if self.component == "backend" else self.component
            if queue == "solver":
                env["DEVFEED_SOLVER_SERVICES"] = (
                    '[{"provider":"flaresolverr","url":"http://unused-solver:8191"}]'
                )
            if queue == "notifications":
                key = "notification-worker"
                inbox_mounts, inbox_env = self.inbox("worker")
                mounts += inbox_mounts
                env.update(inbox_env)
            name = self.start(CONTRACTS[key]["command"], env=env, mounts=mounts)
            probe = ["python", "-m", "devfeed_core.worker_health"]
            self.wait_probe(name, probe)
            self.execute(name, "python", "/fleet/runtime.py", "worker")
            self.execute(name, "python", "/fleet/runtime.py", "permissions")
            self.stop(name)
        print(
            f"PASS native Fleet {self.component} worker commands, "
            f"queue registration and heartbeats ({len(queues)})"
        )

    def scheduler(self):
        self.execute(self.redis, "redis-cli", "-n", "15", "flushdb")
        name = self.start(CONTRACTS["scheduler"]["command"], mounts=self.ca_mounts)
        self.wait_probe(name, CONTRACTS["scheduler"]["probes"]["livenessProbe"])
        self.stop(name)
        print("PASS native Fleet scheduler command and successful-cycle heartbeat")

    def indexer(self):
        name = self.prefix + "-typesense"
        self.containers.append(name)
        command(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            "-e",
            "TYPESENSE_API_KEY=disposable-search-key",
            TYPESENSE,
            "--data-dir=/tmp",
        )
        env = {
            "DEVFEED_SEARCH_URL": f"http://{name}:8108",
            "DEVFEED_SEARCH_ADMIN_KEY": "disposable-search-key",
            "DEVFEED_SEARCH_ENABLED": "true",
        }
        process = self.start(CONTRACTS["indexer"]["command"], env=env)
        self.wait_probe(process, CONTRACTS["indexer"]["probes"]["readinessProbe"], seconds=60)
        self.stop(process)
        print("PASS native Fleet search setup, real Typesense and indexer heartbeat")

    def publishers(self):
        output = self.run(CONTRACTS["backfill"]["command"])
        assert '"cutover_ready": true' in output
        self.run(["python", "/fleet/runtime.py", "projection"])
        for kind in ("pipeline", "reports"):
            name = self.start(CONTRACTS[kind]["command"])
            probe = CONTRACTS[kind]["probes"]["readinessProbe"]
            self.wait_probe(name, probe)
            self.execute(name, "python", "/fleet/runtime.py", "permissions")
            # Stop the publisher before aging its marker, so a fresh cycle cannot race the check.
            command("docker", "kill", "--signal=STOP", name)
            try:
                self.execute(name, "python", "/fleet/runtime.py", "heartbeat", kind)
                assert self.execute(name, *probe, check=False).returncode != 0
            finally:
                command("docker", "kill", "--signal=CONT", name)
            self.stop(name)
        print(
            "PASS native Fleet backfill and both publishers; fresh probes pass, stale probes fail"
        )

    def frontend(self):
        name = self.start(frontend=True)
        probe = ["node", "/fleet/frontend.cjs", "ready", self.component]
        self.wait_probe(name, probe, seconds=60)
        self.execute(name, "node", "/fleet/frontend.cjs", "permissions", self.component)
        if self.component == "web":
            self.execute(name, *CONTRACTS["web-prestop"]["command"])
        self.stop(name)
        print(
            f"PASS native Fleet {self.component} read-only startup/cache/tmp"
            + (" and preStop" if self.component == "web" else "")
        )


def main():
    image, component = sys.argv[1:]
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    actual = command("docker", "image", "inspect", image, "--format", "{{.Architecture}}")
    if actual.stdout.strip() != architecture:
        raise RuntimeError("Fleet smoke requires a native image, not emulation")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    started = time.monotonic()
    print(f"Native Fleet contracts: {component} on {architecture}", flush=True)
    with Smoke(image, component) as smoke:
        if component in {"web", "admin", "partner"}:
            smoke.frontend()
        else:
            smoke.services()
            if component in {"backend", "admin-api"}:
                smoke.ca()
            smoke.schema()
            if component == "backend":
                smoke.workers()
                smoke.indexer()
                smoke.scheduler()
                smoke.api()
            elif component == "admin-api":
                smoke.publishers()
                smoke.api()
            elif component in {"user-api", "partner-api"}:
                smoke.api()
            else:
                smoke.workers()
    print(f"PASS {component} native Fleet contracts in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
