import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "compose_dev_test", Path(__file__).resolve().parents[1] / "scripts" / "compose_dev.py"
)
assert SPEC and SPEC.loader
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


@pytest.fixture
def commands(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        dev,
        "configuration",
        lambda: {"services": {name: {} for name in dev.APPLICATIONS if name != "codex-client"}},
    )
    monkeypatch.setattr(dev, "compose", lambda *args, **kw: recorded.append(args))
    return recorded


def test_build_then_stop_then_migrate_then_recreate_without_data_recreation(commands):
    dev.rebuild()
    assert [c[0] for c in commands] == ["build", "up", "stop", "run", "up"]
    assert commands[1][-2:] == ("postgres", "redis")
    assert "--no-deps" in commands[3] and "migrate" in commands[3]
    assert "--no-deps" in commands[4] and "--force-recreate" in commands[4]
    assert "postgres" not in commands[4] and "redis" not in commands[4]
    assert all("down" not in c and "--volumes" not in c for c in commands)


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("build", ["build"]),
        ("run", ["build", "up", "stop", "run"]),
    ],
)
def test_build_and_migration_failures_stop_the_pipeline(monkeypatch, commands, failure, expected):
    def execute(*args, **kwargs):
        commands.append(args)
        if args[0] == failure:
            raise RuntimeError("failed")

    monkeypatch.setattr(dev, "compose", execute)
    assert dev.attempt_rebuild() is False
    assert [c[0] for c in commands] == expected


def test_missing_codex_login_keeps_existing_apps_running(monkeypatch, commands):
    monkeypatch.setattr(
        dev,
        "configuration",
        lambda: {
            "services": {
                **{name: {} for name in dev.APPLICATIONS},
                "codex-server": {},
                "codex-client": {"environment": {"DEVFEED_AI_ENABLED": "true"}},
            }
        },
    )

    def execute(*args, **kwargs):
        commands.append(args)
        if args[0] == "exec":
            raise RuntimeError("not logged in")

    monkeypatch.setattr(dev, "compose", execute)
    with pytest.raises(RuntimeError, match="device-auth"):
        dev.rebuild()
    assert [c[0] for c in commands] == ["build", "up", "exec"]


def test_environment_updates_are_private_idempotent_and_preserve_other_values(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    env = tmp_path / ".env"
    env.write_text("# Keep this\nPOSTGRES_PASSWORD=keep\nCHANGED=old\nCHANGED=duplicate\n")
    dev.write_env({"CHANGED": "new", "ADDED": "a$\"'\\b"})
    assert "POSTGRES_PASSWORD=keep" in env.read_text()
    assert env.read_text().count("CHANGED=") == 1
    assert env.stat().st_mode & 0o777 == 0o600
    first = env.stat().st_mtime_ns
    dev.write_env({"CHANGED": "new", "ADDED": "a$\"'\\b"})
    assert env.stat().st_mtime_ns == first


def test_watch_tracks_sources_and_env_but_ignores_build_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    for name in ("apps/a/src/main.py", "apps/a/.next/build.js", "apps/a/node_modules/a.js", ".env"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("first")
    before = dev.snapshot()
    assert set(before) == {"apps/a/src/main.py", ".env"}
    (tmp_path / "apps/a/src/main.py").write_text("second-longer")
    assert dev.snapshot() != before


def test_edits_during_a_build_trigger_another_build(monkeypatch):
    state = {"file": (1, 1)}
    builds = []
    monkeypatch.setattr(dev, "snapshot", lambda: dict(state))
    monkeypatch.setattr(dev.time, "sleep", lambda _: None)

    def rebuild():
        builds.append(True)
        if len(builds) == 1:
            state["file"] = (2, 2)
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(dev, "attempt_rebuild", rebuild)
    with pytest.raises(KeyboardInterrupt):
        dev.watch(0.1)
    assert len(builds) == 2


def test_compose_errors_never_echo_captured_credentials(monkeypatch):
    monkeypatch.setattr(
        dev.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1,
            stdout="secret-password",
            stderr="secret-token",
        ),
    )
    with pytest.raises(RuntimeError) as error:
        dev.configuration()
    assert "secret" not in str(error.value)


def test_rendered_configuration_unescapes_dollars_once(monkeypatch):
    monkeypatch.setattr(
        dev,
        "compose",
        lambda *a, **k: SimpleNamespace(
            stdout='{"services":{"chimely":{"environment":{"PASSWORD":"a$$$$b","UNSET":null}}}}',
        ),
    )
    assert dev.configuration()["services"]["chimely"]["environment"] == {
        "PASSWORD": "a$$b",
        "UNSET": None,
    }
