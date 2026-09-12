"""Run full checks for projects affected by the files supplied by pre-commit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK_CONFIG = {".pre-commit-config.yaml", "scripts/pre_commit_checks.py"}
WEB_CONFIG = {
    "package.json",
    "package-lock.json",
    ".prettierrc.json",
    ".prettierignore",
    ".editorconfig",
}


def checks_for(paths: set[str]) -> list[tuple[str, ...]]:
    """Shared code/config changes exercise every consumer; docs alone stay cheap."""
    all_projects = bool(paths & HOOK_CONFIG)
    python = all_projects or any(
        path.endswith((".py", ".pyi", "/pyproject.toml"))
        or path in {"pyproject.toml", "uv.lock", "alembic.ini", ".editorconfig"}
        or path.startswith(("scripts/test.sh", "scripts/ci/python-", ".github/scripts/python-"))
        for path in paths
    )
    shared_web = (
        all_projects
        or bool(paths & WEB_CONFIG)
        or any(path.startswith(("packages/ui/", "packages/theme/")) for path in paths)
    )
    commands: list[tuple[str, ...]] = []
    if python or any(path.endswith(("package.json", "openapi.json")) for path in paths):
        commands.append(("uv", "run", "--locked", "python", "scripts/version.py", "check"))
    if python:
        commands.extend(
            [
                ("uv", "run", "--locked", "ruff", "check", "."),
                ("uv", "run", "--locked", "ruff", "format", "--check", "."),
                ("uv", "run", "--locked", "mypy"),
                ("uv", "run", "--locked", "pytest", "-q", "-m", "not integration"),
            ]
        )
    admin = shared_web or any(
        path.startswith(("apps/admin/", ".github/scripts/admin-web-")) for path in paths
    )
    web = shared_web or any(
        path.startswith(("apps/web/", ".github/scripts/user-web-")) for path in paths
    )
    if admin or web:
        commands.append(("npm", "run", "format:typescript:check"))
    for workspace, affected in (("admin", admin), ("web", web)):
        if affected:
            commands.extend(
                [
                    ("npm", "run", "lint", "--workspace", f"@devfeed/{workspace}"),
                    ("npm", "run", f"{workspace}:test"),
                ]
            )
    if (
        all_projects
        or bool(paths & {"package.json", "package-lock.json"})
        or any(
            path.startswith("infra/codex/") or path == "tests/codex_transport.test.cjs"
            for path in paths
        )
    ):
        commands.append(("node", "--test", "tests/codex_transport.test.cjs"))
    return commands


def main(filenames: list[str]) -> int:
    # pre-commit filters deleted files out of its arguments. Include staged
    # deletions so removing a module still exercises its consumers.
    deleted = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=D", "-z"], cwd=ROOT
    )
    paths = set(filenames) | {os.fsdecode(path) for path in deleted.split(b"\0") if path}
    env = os.environ.copy()
    # Match CI's unit-test isolation even when developers have services in .env.
    env["DEVFEED_DATABASE_URL"] = "postgresql+psycopg://unit@database.invalid/unit_test"
    env["DEVFEED_REDIS_URL"] = "redis://redis.invalid/15"
    env.pop("DEVFEED_TEST_DATABASE_URL", None)
    env.pop("DEVFEED_TEST_REDIS_URL", None)
    for command in checks_for(paths):
        print(f"\nRunning: {' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
