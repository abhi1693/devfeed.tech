"""Exercise the actual installer against first-party and dependency build canaries."""

import os
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BOOTSTRAP = Path(__file__).resolve().parents[1] / "scripts/ci/python-bootstrap.sh"


def project(tmp_path, *, workspace):
    relative = "apps/fixture" if workspace else "external"
    source = tmp_path / relative
    source.mkdir(parents=True)
    (source / "fixture_app.py").write_text("value = 42\n")
    (source / "pyproject.toml").write_text(
        '[project]\nname = "fixture-app"\nversion = "1.0.0"\n'
        'requires-python = ">=3.12"\ndependencies = []\n'
        '[build-system]\nrequires = []\nbuild-backend = "canary_backend"\n'
        'backend-path = ["."]\n'
    )
    (source / "canary_backend.py").write_text(
        textwrap.dedent("""\
        from pathlib import Path
        from zipfile import ZipFile

        def build(wheel_directory, editable=False):
            Path(__file__).with_name("build-ran").write_text("called")
            name = "fixture_app-1.0.0-py3-none-any.whl"
            info = "fixture_app-1.0.0.dist-info"
            files = {
                info + "/METADATA": "Metadata-Version: 2.3\\nName: fixture-app\\nVersion: 1.0.0\\n",
                info + "/WHEEL": (
                    "Wheel-Version: 1.0\\nGenerator: canary\\nRoot-Is-Purelib: true\\n"
                    "Tag: py3-none-any\\n"
                ),
            }
            if editable:
                files["fixture_app.pth"] = str(Path(__file__).parent) + "\\n"
            else:
                files["fixture_app.py"] = "value = 42\\n"
            files[info + "/RECORD"] = (
                "".join(path + ",,\\n" for path in files) + info + "/RECORD,,\\n"
            )
            with ZipFile(Path(wheel_directory) / name, "w") as archive:
                for path, content in files.items():
                    archive.writestr(path, content)
            return name

        def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
            return build(wheel_directory)

        def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
            return build(wheel_directory, editable=True)
        """)
    )
    origin = "workspace = true" if workspace else f'path = "{relative}"'
    members = f'[tool.uv.workspace]\nmembers = ["{relative}"]\n' if workspace else ""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture-root"\nversion = "1.0.0"\n'
        'requires-python = ">=3.12"\ndependencies = ["fixture-app"]\n'
        "[tool.uv]\npackage = false\n"
        f"[tool.uv.sources]\nfixture-app = {{ {origin} }}\n{members}"
    )
    env = {
        **os.environ,
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_OFFLINE": "1",
        "UV_NO_BUILD": "1",
        "UV_NO_SYNC": "1",
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
    }
    locked = subprocess.run(
        ["uv", "lock", "--no-build"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert locked.returncode == 0, locked.stdout + locked.stderr
    assert not (source / "build-ran").exists()
    return source, env


@pytest.mark.parametrize("editable", [False, True])
def test_bootstrap_explicitly_builds_and_installs_first_party_code(tmp_path, editable):
    source, env = project(tmp_path, workspace=True)
    result = subprocess.run(
        [
            "sh",
            str(BOOTSTRAP),
            str(tmp_path),
            *(["--editable"] if editable else []),
            "--all-packages",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (source / "build-ran").read_text() == "called"
    imported = subprocess.run(
        [
            str(tmp_path / ".venv/bin/python"),
            "-I",
            "-c",
            "import fixture_app; from importlib.metadata import version; "
            'print(fixture_app.value, version("fixture-app"), fixture_app.__file__)',
        ],
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
    value, version, filename = imported.stdout.strip().split(maxsplit=2)
    assert (value, version) == ("42", "1.0.0")
    assert (Path(filename) == source / "fixture_app.py") is editable


def test_bootstrap_rejects_dependency_builds_before_running_the_backend(tmp_path):
    source, env = project(tmp_path, workspace=False)
    result = subprocess.run(
        ["sh", str(BOOTSTRAP), str(tmp_path), "--all-packages"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "fixture-app" in result.stderr
    assert not (source / "build-ran").exists()


def test_container_dependency_stage_accepts_package_selection_without_building_workspace(tmp_path):
    source, env = project(tmp_path, workspace=True)
    dockerfile = BOOTSTRAP.parents[2] / "Dockerfile"
    invocation = re.search(
        r"^\s+sh /usr/local/bin/python-dependencies\.sh (.+)$",
        dockerfile.read_text(),
        re.MULTILINE,
    )
    assert invocation is not None
    # Exercise the real dependency-stage flags with a disposable metadata-only
    # workspace. The selected package substitutes the Docker build argument.
    arguments = []
    for argument in shlex.split(invocation.group(1)):
        arguments.extend(
            ["--package", "fixture-app"] if argument == "${DEVFEED_PACKAGE_ARGS}" else [argument]
        )
    result = subprocess.run(
        ["sh", str(BOOTSTRAP.with_name("python-dependencies.sh")), *arguments],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (source / "build-ran").exists()
    imported = subprocess.run(
        [
            str(tmp_path / ".venv/bin/python"),
            "-I",
            "-c",
            'from importlib.util import find_spec; print(find_spec("fixture_app"))',
        ],
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "None"


def test_http_ece_exception_cannot_build_an_unreviewed_package_with_the_same_name(tmp_path):
    source, env = project(tmp_path, workspace=False)
    for configuration in (source / "pyproject.toml", tmp_path / "pyproject.toml"):
        configuration.write_text(configuration.read_text().replace("fixture-app", "http-ece"))
    locked = subprocess.run(
        ["uv", "lock", "--no-build"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert locked.returncode == 0, locked.stdout + locked.stderr
    result = subprocess.run(
        ["sh", str(BOOTSTRAP), str(tmp_path), "--all-packages"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "http-ece" in result.stderr
    assert not (source / "build-ran").exists()
