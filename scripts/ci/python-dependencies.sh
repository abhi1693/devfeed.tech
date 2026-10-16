#!/bin/sh
# Keep dependencies wheel-only except the reviewed, hash-pinned http-ece release.
set -eu
dependencies_project=$1
shift
cd "$dependencies_project"
dependencies_files=$(mktemp -d)
trap 'rm -rf "$dependencies_files"' EXIT
uv export --locked --no-editable --no-hashes --quiet \
  --output-file "$dependencies_files/selected.txt" "$@"
uv sync --locked --no-install-workspace --no-install-package http-ece --no-build "$@"
if grep -Eq '^http-ece(==| @)' "$dependencies_files/selected.txt"; then
  # pywebpush needs http-ece, whose upstream release contains only an sdist.
  # uv's named no-binary exception overrides no-build. Verify its locked archive
  # first so an unrelated path package, registry or future release cannot build.
  .venv/bin/python - <<'PY'
import tomllib
from pathlib import Path

packages = [package for package in tomllib.loads(Path("uv.lock").read_text())["package"]
            if package["name"] == "http-ece"]
if not (len(packages) == 1 and packages[0]["version"] == "1.2.1"
        and packages[0]["source"] == {"registry": "https://pypi.org/simple"}
        and packages[0].get("sdist", {}).get("hash") ==
        "sha256:8c6ab23116bbf6affda894acfd5f2ca0fb8facbcbb72121c11c75c33e7ce8cff"):
    raise SystemExit("Unreviewed http-ece archive; update the explicit build exception.")
PY
  # Runtime and build dependencies remain under no-build; only this pinned
  # package is permitted to execute its source build backend.
  uv sync --locked --no-install-workspace --no-build --no-binary-package http-ece "$@"
else
  uv sync --locked --no-install-workspace --no-build "$@"
fi
