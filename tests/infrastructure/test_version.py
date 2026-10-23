import importlib.util
import json
import subprocess
from importlib.metadata import version

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from devfeed_api.main import create_app
from devfeed_cli.main import run
from devfeed_core.version import SCHEMA_REVISION, __version__
from fastapi.testclient import TestClient

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location("version_command", REPO_ROOT / "scripts/version.py")
assert spec is not None and spec.loader is not None
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


def test_required_schema_revision_matches_migration_head():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_current_head() == SCHEMA_REVISION


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
    for name in command.JSON_MANIFESTS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": name, "version": "0.1.0"}))
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "packages": {
                    "": {"version": "0.1.0"},
                    "apps/admin": {"version": "0.1.0"},
                    "apps/web": {"version": "0.1.0"},
                    "apps/partner": {"version": "0.1.0"},
                },
            }
        )
    )
    write_generated(tmp_path, "0.1.0")
    return tmp_path


def write_lock(root, value):
    (root / "uv.lock").write_text(
        "version = 1\n"
        + "".join(
            f'[[package]]\nname = "project-{i}"\nversion = "{value}"\n'
            for i, _ in enumerate(command.MANIFESTS)
        )
    )


def write_generated(root, value, app=None):
    for name, schema_name, client_name, required in command.API_ARTIFACTS:
        if app is not None and name != app:
            continue
        (root / schema_name).write_text(json.dumps({"info": {"version": value}}))
        for relative in (*required, "models/article.ts"):
            path = root / client_name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"/**\n * OpenAPI spec version: {value}\n */\n")
        for route_app, _, _ in command.GENERATED_ROUTES:
            if name == route_app:
                write_generated_route(root, name, "auth/config", value)


def write_generated_route(root, app, relative, value):
    _, route_root, marker = next(item for item in command.GENERATED_ROUTES if item[0] == app)
    path = root / route_root / relative / "route.ts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"/**\n{marker}\n * OpenAPI spec version: {value}\n */\n"
        "export const GET = generatedHandler;\n"
    )
    return path


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


@pytest.mark.parametrize("ending", ["", '\n[tool.example]\nversion = "independent"\n'])
def test_project_version_stays_within_its_section_for_long_and_unicode_content(ending):
    prelude = '[tool.before]\nversion = "independent"\n'
    body = (
        '[project]\u00a0\t\nname = "Développeur"\n'
        'description = "' + "long [text] # content " * 10_000 + '"\n'
        "version = '0.1.0' # retained punctuation\n"
    )
    original = prelude + body + ending
    assert command.replace_version(original, "0.2.0") == (
        prelude + body.replace("version = '0.1.0'", "version = '0.2.0'") + ending
    )


def test_project_version_cannot_be_taken_from_a_following_section():
    with pytest.raises(ValueError, match="exactly one literal project version"):
        command.replace_version(
            '[project]\nname = "example"\n[tool.test]\nversion = "0.1.0"', "0.2.0"
        )


def test_set_updates_only_project_versions_and_locks_once(workspace):
    calls = []

    def lock(args, *, cwd, check):
        calls.append((args, cwd, check))
        for relative in command.MANIFESTS:
            content = (workspace / relative).read_text()
            assert 'version = "0.2.0" # retained comment' in content
            assert 'description = "Keep 0.1.0 in this description"' in content
            assert 'version = "independent"' in content
        if args == ["uv", "lock", "--offline"]:
            write_lock(workspace, "0.2.0")
        elif args[:2] == ["npm", "run"]:
            write_generated(workspace, "0.2.0", args[2].split(":")[0])

    command.set_version(workspace, "0.2.0", runner=lock)
    assert calls == [
        (["uv", "lock", "--offline"], workspace, True),
        (["uv", "sync", "--all-packages", "--locked", "--offline"], workspace, True),
        (["npm", "run", "admin:generate"], workspace, True),
        (["npm", "run", "partner:generate"], workspace, True),
        (["npm", "run", "web:generate"], workspace, True),
    ]
    assert command.check(workspace) == "0.2.0"
    assert json.loads((workspace / "apps/admin/package.json").read_text())["version"] == "0.2.0"
    assert json.loads((workspace / "package-lock.json").read_text())["version"] == "0.2.0"


def test_frontend_version_drift_is_reported(workspace):
    (workspace / "apps/admin/package.json").write_text('{"version": "0.3.0"}')
    with pytest.raises(ValueError, match="Version drift"):
        command.check(workspace)


def test_dry_run_does_not_write_or_invoke_uv(workspace):
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    original_lock = (workspace / "uv.lock").read_bytes()
    result = command.set_version(
        workspace, "0.1.1", dry_run=True, runner=lambda *a, **kw: pytest.fail("Ran uv")
    )
    assert "Would update" in result
    assert all(p.read_bytes() == content for p, content in before.items())
    assert (workspace / "uv.lock").read_bytes() == original_lock


@pytest.mark.parametrize(
    "relative",
    [
        relative
        for _, schema_name, client_name, required in command.API_ARTIFACTS
        for relative in (
            schema_name,
            *(f"{client_name}/{name}" for name in required),
            f"{client_name}/models/article.ts",
        )
    ]
    + [f"{relative}/auth/config/route.ts" for _, relative, _ in command.GENERATED_ROUTES],
)
def test_generated_version_drift_is_reported(workspace, relative):
    path = workspace / relative
    path.write_text(path.read_text().replace("0.1.0", "0.0.9"))
    with pytest.raises(ValueError, match="Generated version drift"):
        command.check(workspace)


@pytest.mark.parametrize(
    "relative",
    [
        f"{client_name}/{name}"
        for _, _, client_name, required in command.API_ARTIFACTS
        for name in required
    ],
)
def test_missing_generated_client_is_reported(workspace, relative):
    (workspace / relative).unlink()
    with pytest.raises(ValueError, match="Generated version drift"):
        command.check(workspace)


@pytest.mark.parametrize("failure", ["sync", "admin", "partner", "web", "stale-output"])
def test_failed_bump_restores_manifests_locks_and_generated_artifacts(workspace, failure):
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}

    def run(args, *, cwd, check):
        if args == ["uv", "lock", "--offline"]:
            write_lock(workspace, "0.2.0")
        elif args[1] == "sync" and failure == "sync":
            raise subprocess.CalledProcessError(1, args)
        elif args[:2] == ["npm", "run"] and failure != "stale-output":
            app = args[2].split(":")[0]
            write_generated(workspace, "0.2.0", app)
            if failure == app:
                client_name = next(item[2] for item in command.API_ARTIFACTS if item[0] == app)
                (workspace / client_name / "models/new.ts").write_text("partial generation")
                (workspace / client_name / "models/article.ts").unlink()
                raise subprocess.CalledProcessError(1, args)
        # A generator that succeeds but retains old stamps must also fail check().

    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        command.set_version(workspace, "0.2.0", runner=run)
    after = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert after == before
    assert command.check(workspace) == "0.1.0"


@pytest.mark.parametrize("failure", ["generation", "stale-output", "interrupt"])
@pytest.mark.parametrize("app,route_root,marker", command.GENERATED_ROUTES)
def test_failed_bump_restores_only_generator_owned_routes(
    workspace, failure, app, route_root, marker
):
    modified = workspace / route_root / "auth/config/route.ts"
    deleted = write_generated_route(workspace, app, "inventory", "0.1.0")
    handwritten = workspace / route_root / "handwritten/route.ts"
    handwritten.parent.mkdir(parents=True)
    handwritten.write_text("export const GET = handwrittenHandler;\n")
    original_routes = {path: path.read_bytes() for path in (modified, deleted)}
    new_generated = workspace / route_root / "added/route.ts"
    new_handwritten = workspace / route_root / "manual/route.ts"

    def run(args, *, cwd, check):
        if args == ["uv", "lock", "--offline"]:
            write_lock(workspace, "0.2.0")
        elif args[:2] == ["npm", "run"]:
            generated_app = args[2].split(":")[0]
            write_generated(workspace, "0.2.0", generated_app)
            if generated_app == app:
                deleted.unlink()
                write_generated_route(workspace, app, "added", "0.2.0")
                handwritten.write_text("export const GET = independentlyUpdatedHandler;\n")
                new_handwritten.parent.mkdir(parents=True)
                new_handwritten.write_text("export const POST = newHandwrittenHandler;\n")
                if failure == "generation":
                    raise subprocess.CalledProcessError(1, args)
                if failure == "interrupt":
                    raise KeyboardInterrupt
                # Even a nominally successful generator must roll back if a
                # concrete handler retains the old release's version stamp.
                write_generated_route(workspace, app, "added", "0.1.0")

    with pytest.raises((subprocess.CalledProcessError, ValueError, KeyboardInterrupt)):
        command.set_version(workspace, "0.2.0", runner=run)
    assert all(path.read_bytes() == content for path, content in original_routes.items())
    assert not new_generated.exists()
    assert handwritten.read_text() == "export const GET = independentlyUpdatedHandler;\n"
    assert new_handwritten.read_text() == "export const POST = newHandwrittenHandler;\n"
    assert command.check(workspace) == "0.1.0"


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
