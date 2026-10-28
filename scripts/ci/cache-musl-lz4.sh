#!/bin/sh
# Cache only the hash-locked lz4 wheel missing from PyPI for native musl builders.
# Every regular dependency installation retains --no-build.
set -eu
lz4_cache_work=$(mktemp -d)
trap 'rm -rf "$lz4_cache_work"' EXIT
python - "${1:-uv.lock}" "$lz4_cache_work/requirements.txt" <<'PY'
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

lock = tomllib.loads(Path(sys.argv[1]).read_text())
packages = [package for package in lock["package"] if package["name"] == "lz4"]
if len(packages) != 1:
    raise SystemExit("Expected exactly one locked lz4 distribution")
package = packages[0]
source, version, digest = package["sdist"], package["version"], package["sdist"]["hash"]
origin = urlsplit(source["url"])
if (
    package["source"] != {"registry": "https://pypi.org/simple"}
    or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version) is None
    or origin.scheme != "https"
    or origin.hostname != "files.pythonhosted.org"
    or origin.username is not None
    or origin.password is not None
    or origin.query
    or origin.fragment
    or not origin.path.endswith(f"/lz4-{version}.tar.gz")
    or re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is None
):
    raise SystemExit("lz4 must use its hash-locked PyPI source distribution")
Path(sys.argv[2]).write_text(f"lz4=={version} --hash={digest}\n")
PY
# A package-specific source allowance overrides the binary-only default solely
# for lz4. Build dependencies must have wheels; runtime dependencies are excluded.
uv pip install --target "$lz4_cache_work/installed" --no-deps --require-hashes \
  --only-binary :all: --no-binary lz4 --default-index https://pypi.org/simple \
  --requirements "$lz4_cache_work/requirements.txt"
