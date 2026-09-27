"""Publishing must use the release tag's exact successful CI attempt."""

import importlib.util
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "require_release_ci", Path(__file__).resolve().parents[1] / "scripts/ci/require_release_ci.py"
)
release_ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_ci)

SHA = "a" * 40


def test_extension_gate_uses_checked_out_commit_without_local_tag(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/extension-release.yml").read_text()
    assert "github.event_name == 'release' && github.sha || inputs.release_tag" in workflow
    script = textwrap.dedent(workflow.split("        run: |\n", 1)[1].split("  packages:\n", 1)[0])
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "Release",
        ],
        cwd=tmp_path,
        check=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    # Release-event checkout has the commit but no local tag. Capture the actual
    # gate arguments without calling GitHub or waiting for a remote workflow.
    (tmp_path / "python3").write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    (tmp_path / "python3").chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "owner/app",
            "RELEASE_TAG": "v1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "scripts/ci/require_release_ci.py",
        "--repository",
        "owner/app",
        "--sha",
        sha,
        "--tag",
        "v1.2.3",
        "--timeout",
        "5400",
    ]


def run(**overrides):
    return {
        "id": 17,
        "head_sha": SHA,
        "event": "push",
        "head_branch": "v1.2.3",
        "head_repository": {"full_name": "owner/app"},
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 2,
        **overrides,
    }


def gate(**overrides):
    return {
        "name": "CI required",
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        **overrides,
    }


def check(runs, jobs):
    def api(path):
        if "/attempts/" in path:
            assert "/17/attempts/2/jobs?" in path
            return [{"jobs": []}, {"jobs": jobs}]
        assert "head_sha=" + SHA in path
        return [{"workflow_runs": []}, {"workflow_runs": runs}]

    return release_ci.check_release_ci("owner/app", SHA, "v1.2.3", api)


def test_release_requires_matching_run_and_gate_in_latest_attempt():
    assert check([run()], [gate()])


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_sha": "b" * 40},
        {"head_branch": "master"},
        {"event": "pull_request"},
        {"head_repository": {"full_name": "fork/app"}},
        {"status": "in_progress", "conclusion": None},
    ],
)
def test_release_waits_for_its_own_completed_tag_run(overrides):
    assert not check([run(**overrides)], [gate()])


def test_release_waits_for_missing_run():
    assert not check([], [])


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out"])
def test_release_rejects_unsuccessful_run(conclusion):
    with pytest.raises(ValueError, match="ended with"):
        check([run(conclusion=conclusion)], [gate()])


def test_release_does_not_reuse_an_older_success():
    with pytest.raises(ValueError, match="ended with failure"):
        check([run(id=16), run(conclusion="failure")], [gate()])


def test_release_waits_for_newer_run_or_retry_even_after_a_success():
    assert not check([run(id=16), run(status="in_progress", conclusion=None)], [gate()])
    assert not check([run(run_attempt=3, status="queued", conclusion=None)], [gate()])


@pytest.mark.parametrize(
    "jobs",
    [
        [],
        [gate(), gate()],
        [gate(head_sha="b" * 40)],
        [gate(conclusion="failure")],
        [gate(conclusion="skipped")],
        [gate(status="in_progress")],
        [gate(name="Other workflow")],
    ],
)
def test_release_rejects_missing_or_unsuccessful_required_gate(jobs):
    with pytest.raises(ValueError, match="successful CI required"):
        check([run()], jobs)
