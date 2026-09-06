import importlib.util
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest
from devfeed_api.main import create_app
from devfeed_cli.main import run
from devfeed_core.version import SCHEMA_REVISION, __version__
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location(
    "version_command", Path(__file__).resolve().parents[1] / "scripts/version.py"
)
assert spec is not None and spec.loader is not None
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


@pytest.fixture
def workspace(tmp_path):
    for index, relative in enumerate(command.MANIFESTS):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'[project]\nname = "project-{index}"\nversion = "0.1.0" # retained comment\n'
            'description = "Keep 0.1.0 in this description"\n\n'
            '[tool.example]\nversion = "independent"\n'
        )
    write_lock(tmp_path, "0.1.0")
    return tmp_path


def write_lock(root, value):
    (root / "uv.lock").write_text(
        "version = 1\n"
        + "".join(
            f'[[package]]\nname = "project-{i}"\nversion = "{value}"\n'
            for i, _ in enumerate(command.MANIFESTS)
        )
    )


def test_cli_version_needs_no_configuration_or_services(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    with pytest.raises(SystemExit) as caught:
        run(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == f"devfeed {version('devfeed-core')}"


def test_api_and_openapi_report_installed_version_without_database_queries():
    with TestClient(create_app()) as client:
        response = client.get("/version")
        assert response.status_code == 200
        assert response.json() == {
            "version": __version__,
            "required_schema_revision": SCHEMA_REVISION,
        }
        assert response.headers["x-devfeed-version"] == __version__
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/openapi.json").json()["info"]["version"] == __version__


@pytest.mark.parametrize(
    "part,expected", [("major", "1.0.0"), ("minor", "0.2.0"), ("patch", "0.1.1")]
)
def test_bump_calculations(part, expected):
    assert command.next_version("0.1.0", part) == expected


@pytest.mark.parametrize("value", ["", "v1.0.0", "01.0.0", "1.0", "1.0.0rc1", "x", "1.0.0\n"])
def test_release_versions_are_validated(value):
    with pytest.raises(ValueError):
        command.validate(value)


def test_set_updates_only_project_versions_and_locks_once(workspace):
    calls = []

    def lock(args, *, cwd, check):
        calls.append((args, cwd, check))
        for relative in command.MANIFESTS:
            content = (workspace / relative).read_text()
            assert 'version = "0.2.0" # retained comment' in content
            assert 'description = "Keep 0.1.0 in this description"' in content
            assert 'version = "independent"' in content
        write_lock(workspace, "0.2.0")

    command.set_version(workspace, "0.2.0", runner=lock)
    assert calls == [(["uv", "lock", "--offline"], workspace, True)]
    assert command.check(workspace) == "0.2.0"


def test_dry_run_does_not_write_or_invoke_uv(workspace):
    before = {p: p.read_bytes() for p in workspace.rglob("*.toml")}
    original_lock = (workspace / "uv.lock").read_bytes()
    result = command.set_version(
        workspace, "0.1.1", dry_run=True, runner=lambda *a, **kw: pytest.fail("Ran uv")
    )
    assert "Would update" in result
    assert all(p.read_bytes() == content for p, content in before.items())
    assert (workspace / "uv.lock").read_bytes() == original_lock


def test_lock_failure_rolls_back_own_manifest_and_lock_edits(workspace):
    before = {p: p.read_bytes() for p in workspace.rglob("*.toml")}
    original_lock = (workspace / "uv.lock").read_bytes()

    def fail(*a, **kw):
        write_lock(workspace, "0.2.0")
        raise subprocess.CalledProcessError(1, ["uv", "lock"])

    with pytest.raises(subprocess.CalledProcessError):
        command.set_version(workspace, "0.2.0", runner=fail)
    assert all(p.read_bytes() == content for p, content in before.items())
    assert (workspace / "uv.lock").read_bytes() == original_lock


def test_failed_bump_preserves_concurrent_unrelated_manifest_edit(workspace):
    target = workspace / "apps/api/pyproject.toml"

    def fail(*a, **kw):
        target.write_text(target.read_text() + "# Independent edit\n")
        raise subprocess.CalledProcessError(1, ["uv", "lock"])

    with pytest.raises(subprocess.CalledProcessError):
        command.set_version(workspace, "0.2.0", runner=fail)
    assert "# Independent edit" in target.read_text()


def test_manifest_and_lock_drift_are_reported(workspace):
    path = workspace / "apps/api/pyproject.toml"
    original = path.read_text()
    path.write_text(original.replace('version = "0.1.0"', 'version = "0.2.0"'))
    with pytest.raises(ValueError, match="drift"):
        command.check(workspace)
    path.write_text(original)
    write_lock(workspace, "0.2.0")
    with pytest.raises(ValueError, match="Lockfile"):
        command.check(workspace)


@pytest.mark.parametrize("value", ["0.1.0", "0.0.9"])
def test_cannot_reuse_or_lower_a_release_version(workspace, value):
    with pytest.raises(ValueError, match="greater"):
        command.set_version(workspace, value)
