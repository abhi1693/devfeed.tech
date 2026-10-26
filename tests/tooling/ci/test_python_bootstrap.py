"""Exercise the actual installer against first-party and dependency build canaries."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT

BOOTSTRAP = REPO_ROOT / "scripts/ci/python-bootstrap.sh"
CACHE_LZ4 = REPO_ROOT / "scripts/ci/cache-musl-lz4.sh"


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


@pytest.mark.parametrize(
    "name,registry,source_hash,expected",
    [
        ("lz4", "https://pypi.org/simple", "sha256:" + "a" * 64, True),
        ("unapproved", "https://pypi.org/simple", "sha256:" + "a" * 64, False),
        ("lz4", "https://untrusted.invalid/simple", "sha256:" + "a" * 64, False),
        ("lz4", "https://pypi.org/simple", "md5:" + "a" * 32, False),
    ],
)
def test_musl_cache_builds_only_hash_locked_pypi_lz4(
    tmp_path, name, registry, source_hash, expected
):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        f'[[package]]\nname = "{name}"\nversion = "4.4.5"\n'
        f'source = {{registry = "{registry}"}}\n'
        'sdist = {url = "https://files.pythonhosted.org/packages/lz4-4.4.5.tar.gz", '
        f'hash = "{source_hash}"}}\n'
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python").symlink_to(sys.executable)
    capture = tmp_path / "installer.json"
    installer = binaries / "uv"
    installer.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        'requirements = sys.argv[sys.argv.index("--requirements") + 1]\n'
        'Path(os.environ["LZ4_TEST_CAPTURE"]).write_text(json.dumps({'
        '"arguments": sys.argv[1:], "requirements": Path(requirements).read_text()}))\n'
    )
    installer.chmod(0o755)
    result = subprocess.run(
        ["sh", str(CACHE_LZ4), str(lock)],
        env={
            **os.environ,
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "LZ4_TEST_CAPTURE": str(capture),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is expected
    assert capture.exists() is expected
    if expected:
        recorded = json.loads(capture.read_text())
        arguments = recorded["arguments"]
        assert arguments[:2] == ["pip", "install"]
        assert "--no-deps" in arguments and "--require-hashes" in arguments
        assert arguments[arguments.index("--only-binary") + 1] == ":all:"
        assert arguments[arguments.index("--no-binary") + 1] == "lz4"
        assert recorded["requirements"] == "lz4==4.4.5 --hash=sha256:" + "a" * 64 + "\n"


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
