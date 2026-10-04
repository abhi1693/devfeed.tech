"""Keep deployment owners separate while permitting explicit worker/CLI adapters."""

import ast
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# These apps are launchers/operators for existing worker entry points. Domain
# libraries and HTTP services must never depend on another application package.
ADAPTER_IMPORTS = {
    "devfeed_cli": {"devfeed_aggregator", "devfeed_search_indexer"},
    "devfeed_article_enrichment_worker": {"devfeed_aggregator"},
    "devfeed_images_worker": {"devfeed_aggregator"},
    "devfeed_source_discovery_worker": {"devfeed_aggregator"},
}


def test_backend_imports_follow_service_ownership():
    violations = []
    workspace = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]
    roots = [ROOT / member / "src" for member in workspace["members"]]
    for root in roots:
        configuration = tomllib.loads((root.parent / "pyproject.toml").read_text())
        package_dirs = configuration.get("tool", {}).get("setuptools", {}).get("package-dir", {})
        if not package_dirs:
            continue
        owner = next(iter(package_dirs))
        allowed = {owner, "devfeed_core"}
        if owner != "devfeed_core":
            allowed.add("devfeed_http")
        allowed.update(ADAPTER_IMPORTS.get(owner, set()))
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module
                    else [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for name in names:
                    target = name.split(".")[0]
                    if target.startswith("devfeed_") and target not in allowed:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {target}")
    assert not violations, "Application coupling:\n" + "\n".join(violations)


@pytest.mark.parametrize("package", ["devfeed_api", "devfeed_admin_api", "devfeed_user_api"])
def test_loading_an_api_does_not_load_another_application(package, tmp_path):
    code = (
        "import importlib,json,sys; "
        f"importlib.import_module('{package}.main'); "
        "print(json.dumps(sorted({name.split('.')[0] for name in sys.modules "
        "if name.startswith('devfeed_')})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "DEVFEED_DATABASE_URL": "postgresql+psycopg://ci@database.invalid/ci",
            "DEVFEED_REDIS_URL": "redis://redis.invalid/15",
        },
    )
    assert set(json.loads(result.stdout)) <= {package, "devfeed_core", "devfeed_http"}
