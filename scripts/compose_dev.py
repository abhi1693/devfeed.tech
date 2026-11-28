#!/usr/bin/env python3
"""Build, migrate and recreate the local stack; optionally watch source changes."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.build.yaml"]
APPLICATIONS = [
    "admin",
    "admin-api",
    "api",
    "scheduler",
    "codex-client",
]
IGNORED = {
    ".git",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "reports",
    ".idea",
}
WATCH_ROOTS = ["apps", "packages", "migrations", "infra", "scripts"]
WATCH_FILES = [
    ".env",
    ".dockerignore",
    "Dockerfile",
    "compose.yaml",
    "compose.build.yaml",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "alembic.ini",
    ".python-version",
]


def compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [*COMPOSE, *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        # Config may contain credentials. Never include captured output in errors.
        raise RuntimeError(f"Compose {args[0]} failed (exit {result.returncode})")
    return result


def configuration() -> dict:
    config = json.loads(compose("config", "--format", "json", capture=True).stdout)
    # Compose escapes dollar signs in rendered config so it can be reused as YAML.
    # Provisioning needs the actual environment values received by containers.
    for service in config["services"].values():
        for key, value in service.get("environment", {}).items():
            if isinstance(value, str):
                service["environment"][key] = value.replace("$$", "$")
    return config


def interpolation_environment() -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in compose("config", "--environment", capture=True).stdout.splitlines()
        if "=" in line
    )


def write_env(values: dict[str, str]) -> None:
    """Atomically replace only named settings, retaining unrelated local content."""
    path = ROOT / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    pending = dict(values)
    output = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in values:
            if key in pending:
                output.append(env_line(key, pending.pop(key)))
        else:
            output.append(line)
    output.extend(env_line(key, value) for key, value in pending.items())
    content = "\n".join(output) + "\n"
    if path.exists() and path.read_text() == content:
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".env.", dir=ROOT)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def env_line(key: str, value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Environment values must be single-line strings")
    escaped = json.dumps(value, ensure_ascii=False).replace("$", "\\$")
    return f"{key}={escaped}"


def enable_profiles(*, ai: bool) -> None:
    if not ai:
        return
    current = interpolation_environment()
    profiles = set(filter(None, current.get("COMPOSE_PROFILES", "").split(",")))
    profiles.add("ai")
    values = {
        "DEVFEED_AI_ENABLED": "true",
        "DEVFEED_WORKER_QUEUE": "background",
        "DEVFEED_CODEX_APP_SERVER_URL": "unix:///run/codex/app-server.sock",
        "DEVFEED_CODEX_MODEL": current.get("DEVFEED_CODEX_MODEL") or "gpt-6-sol",
    }
    values["COMPOSE_PROFILES"] = ",".join(sorted(profiles))
    write_env(values)


def rebuild() -> None:
    services = configuration()["services"]
    workers = [name for name in services if name.endswith("-worker")]
    targets = ["migrate", "api", "scheduler", "admin-api", "admin", *workers]
    if "codex-server" in services:
        targets.extend(("codex-server", "codex-client"))
    print("Building replacement images; current app containers keep running.", flush=True)
    compose("build", *targets)
    if "codex-server" in services:
        compose("up", "-d", "--wait", "codex-server")
        ai_enabled = services["codex-client"]["environment"].get("DEVFEED_AI_ENABLED", "false")
        if str(ai_enabled).lower() in {"true", "1"}:
            try:
                compose("exec", "-T", "codex-server", "codex", "login", "status", capture=True)
            except RuntimeError:
                print(
                    "Codex needs sign-in. Once the app is running, open AI connection "
                    "in the admin header and choose Connect ChatGPT.",
                    flush=True,
                )
    applications = [name for name in APPLICATIONS if name in services] + workers
    compose("up", "-d", "--wait", "postgres", "redis")
    print("Stopping app processes before migration; data services remain running.", flush=True)
    compose("stop", *applications)
    compose("run", "--rm", "--no-deps", "migrate")
    # Recreate the one-off migration service too, including after a failed prior run.
    compose("up", "-d", "--wait", "--no-deps", "--force-recreate", "migrate", *applications)
    print("Local images are running and healthy.", flush=True)


def snapshot() -> dict[str, tuple[int, int]]:
    files = [ROOT / name for name in WATCH_FILES]
    for name in WATCH_ROOTS:
        for directory, children, names in os.walk(ROOT / name):
            children[:] = [c for c in children if c not in IGNORED and not c.endswith(".egg-info")]
            files.extend(
                Path(directory) / n
                for n in names
                if not n.endswith((".pyc", ".tsbuildinfo", ".swp", "~"))
                and not n.startswith(".env")
            )
    result = {}
    for path in files:
        try:
            stat = path.stat()
            result[str(path.relative_to(ROOT))] = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            pass
    return result


def watch(debounce: float) -> None:
    # Snapshot before a build, so edits made during the build trigger another pass.
    previous = snapshot()
    attempt_rebuild()
    print(
        "Watching source/config changes. Ctrl-C stops watching and leaves containers running.",
        flush=True,
    )
    while True:
        time.sleep(1)
        current = snapshot()
        if current == previous:
            continue
        while True:
            time.sleep(debounce)
            settled = snapshot()
            if settled == current:
                break
            current = settled
        previous = current
        print("Source/config changed.", flush=True)
        attempt_rebuild()


def attempt_rebuild() -> bool:
    try:
        rebuild()
        return True
    except RuntimeError as error:
        print(str(error), file=sys.stderr, flush=True)
        print("Fix the error and retry. No database volumes were removed.", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Rebuild after source/config edits")
    parser.add_argument(
        "--ai", action="store_true", help="Enable bundled Codex and its analysis client"
    )
    parser.add_argument("--debounce", type=float, default=2, help="Quiet seconds before rebuilding")
    args = parser.parse_args()
    if args.debounce < 0.1:
        parser.error("--debounce must be at least 0.1 seconds")
    try:
        # Keep plain `docker compose` commands on this checkout's local images too.
        write_env({"COMPOSE_FILE": os.pathsep.join(("compose.yaml", "compose.build.yaml"))})
        enable_profiles(ai=args.ai)
        if args.watch:
            watch(args.debounce)
            return 0
        return 0 if attempt_rebuild() else 1
    except KeyboardInterrupt:
        print("\nStopped. Existing containers are retained.")
        return 130
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
