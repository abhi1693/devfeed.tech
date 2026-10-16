#!/bin/sh
# Install locked dependencies; explicitly build the selected first-party workspace sources.
set -eu
bootstrap_scripts=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bootstrap_project=$1
shift
bootstrap_editable=false
case "${1:-}" in
  --editable) bootstrap_editable=true; shift ;;
esac
cd "$bootstrap_project"
bootstrap_wheels=$(mktemp -d)
trap 'rm -rf "$bootstrap_wheels"' EXIT
sh "$bootstrap_scripts/python-dependencies.sh" . "$@"
uv export --locked --no-editable --no-hashes --quiet \
  --output-file "$bootstrap_wheels/workspace.txt" "$@"
while IFS= read -r bootstrap_source; do
  case "$bootstrap_source" in
    ./apps/*|./packages/*)
      if "$bootstrap_editable"; then
        uv pip install --python .venv/bin/python --no-build --no-deps --editable "$bootstrap_source"
      else
        uv build --wheel --no-build --out-dir "$bootstrap_wheels" "$bootstrap_source"
      fi
      ;;
  esac
done < "$bootstrap_wheels/workspace.txt"
if ! "$bootstrap_editable"; then
  uv pip install --python .venv/bin/python --no-build --no-deps "$bootstrap_wheels"/*.whl
fi
