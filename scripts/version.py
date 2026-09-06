"""Keep one release version across workspace manifests and the uv lockfile.

This developer command never touches git, migrations, databases, or services.
"""

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    "pyproject.toml",
    "packages/core/pyproject.toml",
    "apps/api/pyproject.toml",
    "apps/aggregator/pyproject.toml",
    "apps/cli/pyproject.toml",
)
RELEASE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
PROJECT = re.compile(r"(?ms)^\[project\][^\S\n]*\n(?P<body>.*?)(?=^\[|\Z)")
VERSION = re.compile(r"(?m)^(version\s*=\s*)([\"'])([^\"'\n]+)(\2)")


def validate(value: str) -> str:
    if not RELEASE.fullmatch(value):
        raise ValueError("Use a release version MAJOR.MINOR.PATCH, such as 0.2.0")
    return value


def manifests(root: Path) -> dict[Path, dict]:
    return {root / name: tomllib.loads((root / name).read_text()) for name in MANIFESTS}


def current(root: Path) -> str:
    projects = manifests(root)
    expected = validate(projects[root / "pyproject.toml"]["project"]["version"])
    for path, document in projects.items():
        if document["project"]["version"] != expected:
            raise ValueError(f"Version drift in {path.relative_to(root)}; expected {expected}")
    return expected


def check(root: Path) -> str:
    expected = current(root)
    lock = tomllib.loads((root / "uv.lock").read_text())
    for document in manifests(root).values():
        name = document["project"]["name"]
        versions = [item["version"] for item in lock["package"] if item["name"] == name]
        if versions != [expected]:
            raise ValueError(f"Lockfile version drift for {name}; run uv lock")
    return expected


def next_version(value: str, part: str) -> str:
    major, minor, patch = map(int, validate(value).split("."))
    return {
        "major": f"{major + 1}.0.0",
        "minor": f"{major}.{minor + 1}.0",
        "patch": f"{major}.{minor}.{patch + 1}",
    }[part]


def replace_version(content: str, value: str) -> str:
    section = PROJECT.search(content)
    if section is None:
        raise ValueError("Missing [project] section")
    body, count = VERSION.subn(
        lambda match: f"{match[1]}{match[2]}{value}{match[4]}", section["body"]
    )
    if count != 1:
        raise ValueError("Expected exactly one literal project version")
    return content[: section.start("body")] + body + content[section.end("body") :]


def set_version(root: Path, value: str, *, dry_run=False, runner=subprocess.run) -> str:
    previous, target = check(root), validate(value)
    if tuple(map(int, target.split("."))) <= tuple(map(int, previous.split("."))):
        raise ValueError(f"New version must be greater than {previous}")
    originals = {path: path.read_bytes() for path in manifests(root)}
    updated = {
        path: replace_version(content.decode(), target).encode()
        for path, content in originals.items()
    }
    if dry_run:
        return f"Would update all workspace packages: {previous} -> {target}"
    lock_path = root / "uv.lock"
    original_lock = lock_path.read_bytes()
    try:
        # One mechanical edit across the manifests, followed by a single lock.
        # --offline prevents unrelated registry activity during a version bump.
        for path, content in updated.items():
            path.write_bytes(content)
        runner(["uv", "lock", "--offline"], cwd=root, check=True)
        check(root)
    except (Exception, KeyboardInterrupt):
        for path, content in originals.items():
            # Do not overwrite an independent edit made while uv was running.
            if path.read_bytes() == updated[path]:
                path.write_bytes(content)
        lock_path.write_bytes(original_lock)
        raise
    return f"Updated all workspace packages: {previous} -> {target}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Verify workspace and lockfile versions agree")
    setting = commands.add_parser("set", help="Set the next release version across the monorepo")
    setting.add_argument("value")
    bump = commands.add_parser("bump", help="Increment all workspace release versions")
    bump.add_argument("part", choices=["major", "minor", "patch"])
    for command in (setting, bump):
        command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(f"DevFeed {check(ROOT)}: workspace and lockfile versions agree")
        else:
            target = args.value if args.command == "set" else next_version(current(ROOT), args.part)
            print(set_version(ROOT, target, dry_run=args.dry_run))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
