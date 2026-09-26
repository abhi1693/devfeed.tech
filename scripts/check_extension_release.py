"""Require a new extension version when a release changes extension-facing code."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def run(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()


def version(value: str) -> tuple[int, ...]:
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise ValueError(f"Extension version must be numeric semver: {value}") from exc
    if len(parts) != 3 or any(part < 0 for part in parts):
        raise ValueError(f"Extension version must be numeric semver: {value}")
    return parts


def extension_facing(path: str) -> bool:
    return (
        path.startswith("apps/extensions/src/")
        or path.startswith("apps/extensions/chrome/")
        or path
        in {
            "apps/extensions/build.mjs",
            "apps/extensions/package.py",
            "apps/extensions/tsconfig.json",
        }
        or path.startswith("apps/web/src/components/")
        or path.startswith("apps/web/src/lib/")
        or path == "apps/web/src/app/globals.css"
        or path.startswith("packages/ui/")
        or path.startswith("packages/theme/")
    )


def manifest_version(contents: str) -> str:
    return json.loads(contents)["version"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-tag", required=True, help="Previous application release tag")
    parser.add_argument("--release-tag", required=True, help="Application release being checked")
    args = parser.parse_args()

    changed = run(
        "git", "diff", "--name-only", f"{args.base_tag}...{args.release_tag}"
    ).splitlines()
    relevant = [path for path in changed if extension_facing(path)]
    current = manifest_version(Path("apps/extensions/chrome/manifest.json").read_text())
    previous = manifest_version(
        run("git", "show", f"{args.base_tag}:apps/extensions/chrome/manifest.json")
    )

    if version(current) < version(previous):
        raise SystemExit(f"Extension version regressed from {previous} to {current}.")
    if relevant and version(current) <= version(previous):
        paths = "\n".join(f"  - {path}" for path in relevant[:20])
        more = "\n  - …" if len(relevant) > 20 else ""
        raise SystemExit(
            f"Extension-facing files changed since {args.base_tag}, but the extension version "
            f"is still {current}. Increment apps/extensions/chrome/manifest.json before release.\n"
            f"Changed paths:\n{paths}{more}"
        )

    print(
        f"Extension release check passed: {previous} -> {current}; "
        f"{len(relevant)} extension-facing path(s) changed."
    )


if __name__ == "__main__":
    main()
