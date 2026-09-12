"""Docker pull diagnostics must never become a disposable resource identifier."""

import os
import subprocess

import pytest
from test_database_pooler_recovery import docker


def test_docker_keeps_cold_pull_progress_out_of_container_id(tmp_path, monkeypatch):
    executable = tmp_path / "docker"
    executable.write_text(
        '#!/bin/sh\nprintf "Unable to find image locally\\nPull complete\\n" >&2\n'
        'printf "fixture-container-id\\n"\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert docker("run", "-d", "--rm", "fixture-image") == "fixture-container-id"


def test_docker_preserves_failure_diagnostics(tmp_path, monkeypatch):
    executable = tmp_path / "docker"
    executable.write_text('#!/bin/sh\nprintf "Fixture image pull failed\\n" >&2\nexit 1\n')
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(subprocess.CalledProcessError) as error:
        docker("run", "fixture-image")
    assert error.value.stderr == "Fixture image pull failed\n"
