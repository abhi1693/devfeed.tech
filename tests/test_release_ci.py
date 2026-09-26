"""Publishing must use the release tag's exact successful CI attempt."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "require_release_ci", Path(__file__).resolve().parents[1] / "scripts/ci/require_release_ci.py"
)
release_ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_ci)

SHA = "a" * 40


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
