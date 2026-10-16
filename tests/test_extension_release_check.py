"""Release checks resolve literal refs before constructing git revision operands."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_extension_release", ROOT / "scripts/check_extension_release.py"
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.fixture
def release_repository(tmp_path, monkeypatch):
    # Only this disposable repository may own refs, hooks, or an index.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Release test",
                "-c",
                "user.email=release@example.com",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q")
    manifest = tmp_path / "apps/extensions/chrome/manifest.json"
    manifest.parent.mkdir(parents=True)
    for version in ("1.0.0", "1.0.1"):
        manifest.write_text(json.dumps({"version": version}))
        git("add", ".")
        git("commit", "-q", "-m", version)
        git("tag", f"v{version}")
    return tmp_path


@pytest.mark.parametrize("base", ["v1.0.0", "refs/tags/v1.0.0", "HEAD^"])
def test_release_check_accepts_existing_tag_and_revision_operands(
    release_repository, monkeypatch, capsys, base
):
    monkeypatch.setattr(
        sys, "argv", ["check_extension_release.py", "--base-tag", base, "--release-tag", "v1.0.1"]
    )
    check.main()
    assert "1.0.0 -> 1.0.1" in capsys.readouterr().out


@pytest.mark.parametrize("operand", ["base", "release"])
def test_option_shaped_ref_cannot_write_diff_output(release_repository, monkeypatch, operand):
    target = release_repository / "must-not-overwrite"
    target.write_text("retained")
    option = f"--output={target}"
    base, release = (option, "v1.0.1") if operand == "base" else ("v1.0.0", option)
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_extension_release.py", f"--base-tag={base}", f"--release-tag={release}"],
    )
    with pytest.raises(subprocess.CalledProcessError):
        check.main()
    assert target.read_text() == "retained"
    assert not Path(f"{target}...v1.0.1").exists()


@pytest.mark.parametrize(
    "script", ["check_extension_release.py", "sync_extension_release_assets.py"]
)
def test_command_runner_keeps_metacharacters_in_one_literal_argument(monkeypatch, script):
    spec = importlib.util.spec_from_file_location("release_tool_runner", ROOT / "scripts" / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return " result \n"

    monkeypatch.setattr(module.subprocess, "check_output", execute)
    value = "$(touch unexpected); --repo=other/project"
    assert module.run("gh", "release", "view", "--", value) == "result"
    assert calls == [(("gh", "release", "view", "--", value), {"text": True})]
