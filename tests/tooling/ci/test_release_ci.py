"""Release publication reuses trusted master proof for the exact tagged source."""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap

import pytest
import yaml

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "require_release_ci", REPO_ROOT / "scripts/ci/require_release_ci.py"
)
release_ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_ci)

SHA = "a" * 40
REPOSITORY = "owner/app"
TAG = "v1.2.3"
CODEQL_GATES = (
    "Security checks / security / codeql (python)",
    "Security checks / security / codeql (actions)",
    "Security checks / JavaScript and TypeScript CodeQL",
)


def workflow(name="ci.yml", **overrides):
    return {"id": 12, "path": f".github/workflows/{name}", "state": "active", **overrides}


def run(name="ci.yml", **overrides):
    return {
        "id": 17,
        "head_sha": SHA,
        "event": "push",
        "head_branch": "master" if name == "ci.yml" else TAG,
        "head_repository": {"full_name": REPOSITORY},
        "workflow_id": 12,
        "path": f".github/workflows/{name}",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 2,
        **overrides,
    }


def gate(name="CI required", **overrides):
    return {
        "name": name,
        "run_id": 17,
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        **overrides,
    }


def master_gates():
    return [gate(), *(gate(name) for name in CODEQL_GATES)]


def check(runs, jobs, *, source=True, definition=None):
    name = "ci.yml" if source else "release.yml"

    def api(path):
        if path.endswith(f"/workflows/{name}"):
            return [definition or workflow(name)]
        if "/attempts/" in path:
            assert "/17/attempts/2/jobs?" in path
            return [{"jobs": []}, {"jobs": jobs}]
        assert f"/workflows/{name}/runs?" in path
        assert "head_sha=" + SHA in path and "event=push" in path
        assert "branch=" + ("master" if source else TAG) in path
        return [{"workflow_runs": []}, {"workflow_runs": runs}]

    if source:
        return release_ci.check_master_ci(REPOSITORY, SHA, api)
    return release_ci.check_release_ci(REPOSITORY, SHA, TAG, api)


def test_master_proof_requires_current_attempt_ci_and_all_codeql_gates():
    assert check([run()], master_gates())
    assert set(release_ci.WORKFLOWS["ci.yml"][1]) == {"CI required", *CODEQL_GATES}


def test_extension_waits_for_release_images_instead_of_repeating_master_tests():
    assert check([run("release.yml")], [gate("Release required")], source=False)
    with pytest.raises(ValueError, match="successful Release required"):
        check([run("release.yml")], [gate()], source=False)
    assert not check([run()], [gate()], source=False)


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_sha": "b" * 40},
        {"head_branch": TAG},
        {"event": "pull_request"},
        {"event": "workflow_dispatch"},
        {"head_repository": {"full_name": "fork/app"}},
        {"workflow_id": 999},
        {"path": ".github/workflows/other.yml"},
        {"status": "in_progress", "conclusion": None},
    ],
)
def test_master_does_not_accept_foreign_or_incomplete_proof(overrides):
    assert not check([run(**overrides)], master_gates())


@pytest.mark.parametrize("overrides", [{"state": "disabled_manually"}, {"path": "elsewhere.yml"}])
def test_proof_requires_active_canonical_workflow(overrides):
    with pytest.raises(ValueError, match="active canonical workflow"):
        check([run()], master_gates(), definition=workflow(**overrides))


def test_waits_for_missing_run():
    assert not check([], [])


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out"])
def test_rejects_unsuccessful_run(conclusion):
    with pytest.raises(ValueError, match="ended with"):
        check([run(conclusion=conclusion)], master_gates())


def test_does_not_reuse_an_older_success():
    with pytest.raises(ValueError, match="ended with failure"):
        check([run(id=16), run(conclusion="failure")], master_gates())


def test_waits_for_newer_run_or_retry_even_after_a_success():
    assert not check([run(id=16), run(status="in_progress", conclusion=None)], master_gates())
    assert not check([run(run_attempt=3, status="queued", conclusion=None)], master_gates())


@pytest.mark.parametrize("name", ["CI required", *CODEQL_GATES])
@pytest.mark.parametrize(
    "overrides",
    [
        {"head_sha": "b" * 40},
        {"run_id": 16},
        {"conclusion": "failure"},
        {"conclusion": "skipped"},
        {"status": "in_progress"},
    ],
)
def test_rejects_wrong_source_attempt_or_unsuccessful_required_proof(name, overrides):
    jobs = [gate(name, **overrides) if job["name"] == name else job for job in master_gates()]
    with pytest.raises(ValueError, match="one successful"):
        check([run()], jobs)


@pytest.mark.parametrize("name", ["CI required", *CODEQL_GATES])
@pytest.mark.parametrize("duplicate", [False, True])
def test_rejects_missing_or_duplicated_required_proof(name, duplicate):
    jobs = [job for job in master_gates() if job["name"] != name]
    if duplicate:
        jobs.extend([gate(name), gate(name)])
    with pytest.raises(ValueError, match="one successful"):
        check([run()], jobs)


@pytest.fixture
def source_context(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1.2.3"\n')
    monkeypatch.setattr(release_ci.subprocess, "check_output", lambda *args, **kwargs: SHA + "\n")
    env = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_TYPE": "tag",
        "GITHUB_REF": f"refs/tags/{TAG}",
        "GITHUB_SHA": SHA,
    }
    responses = {
        f"repos/{REPOSITORY}/git/ref/tags/{TAG}": {
            "ref": f"refs/tags/{TAG}",
            "object": {"type": "commit", "sha": SHA},
        },
        f"repos/{REPOSITORY}/compare/{SHA}...master": {
            "status": "ahead",
            "base_commit": {"sha": SHA},
        },
    }

    def verify(**overrides):
        return release_ci.verify_release_source(
            REPOSITORY, SHA, TAG, lambda path: [responses[path]], tmp_path, env | overrides
        )

    return verify, env, responses, tmp_path


def test_source_requires_exact_remote_tag_version_checkout_and_master_ancestry(source_context):
    verify, _, _, _ = source_context
    verify()


def test_source_accepts_annotated_tags(source_context):
    verify, _, responses, _ = source_context
    responses[f"repos/{REPOSITORY}/git/ref/tags/{TAG}"]["object"] = {"type": "tag", "sha": "b" * 40}
    responses[f"repos/{REPOSITORY}/git/tags/{'b' * 40}"] = {
        "object": {"type": "commit", "sha": SHA}
    }
    verify()


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_REPOSITORY", "fork/app"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF_TYPE", "branch"),
        ("GITHUB_REF", f"refs/heads/{TAG}"),
        ("GITHUB_SHA", "b" * 40),
    ],
)
def test_source_rejects_non_tag_or_foreign_events(source_context, key, value):
    verify, _, _, _ = source_context
    with pytest.raises(ValueError, match="exact tag push event"):
        verify(**{key: value})


def test_source_rejects_version_mismatch(source_context):
    verify, _, _, root = source_context
    (root / "pyproject.toml").write_text('[project]\nversion="1.2.4"\n')
    with pytest.raises(ValueError, match="checked-out application version"):
        verify()


@pytest.mark.parametrize(
    "tag", ["master", "v1.2", "v01.2.3", "v1.2.3-01", "v1.2.3-rc..1", "v1.2.3+build", "v1.2.3/evil"]
)
def test_source_rejects_invalid_release_tags(source_context, tag):
    _, env, _, root = source_context
    with pytest.raises(ValueError, match="vMAJOR.MINOR.PATCH"):
        release_ci.verify_release_source(REPOSITORY, SHA, tag, lambda path: [], root, env)


@pytest.mark.parametrize("tag", ["v1.2.3-rc.1", "v1.2.3-1", "v1.2.3-alpha-beta"])
def test_source_preserves_semver_prerelease_version_parity(source_context, tag):
    _, env, responses, root = source_context
    (root / "pyproject.toml").write_text(f'[project]\nversion="{tag[1:]}"\n')
    responses[f"repos/{REPOSITORY}/git/ref/tags/{tag}"] = {
        "ref": f"refs/tags/{tag}",
        "object": {"type": "commit", "sha": SHA},
    }
    release_ci.verify_release_source(
        REPOSITORY,
        SHA,
        tag,
        lambda path: [responses[path]],
        root,
        env | {"GITHUB_REF": f"refs/tags/{tag}"},
    )


def test_source_rejects_mismatched_checkout(source_context, monkeypatch):
    verify, _, _, _ = source_context
    monkeypatch.setattr(release_ci.subprocess, "check_output", lambda *args, **kwargs: "b" * 40)
    with pytest.raises(ValueError, match="checkout does not match"):
        verify()


@pytest.mark.parametrize(
    "target", [{"type": "commit", "sha": "b" * 40}, {"type": "tree", "sha": SHA}]
)
def test_source_rejects_moved_or_non_commit_tag(source_context, target):
    verify, _, responses, _ = source_context
    responses[f"repos/{REPOSITORY}/git/ref/tags/{TAG}"]["object"] = target
    with pytest.raises(ValueError, match="remote release tag no longer matches"):
        verify()


def test_source_rejects_tag_object_cycles(source_context):
    verify, _, responses, _ = source_context
    target = {"type": "tag", "sha": "b" * 40}
    responses[f"repos/{REPOSITORY}/git/ref/tags/{TAG}"]["object"] = target
    responses[f"repos/{REPOSITORY}/git/tags/{'b' * 40}"] = {"object": target}
    with pytest.raises(ValueError, match="remote release tag no longer matches"):
        verify()


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_source_rejects_commit_outside_master_history(source_context, status):
    verify, _, responses, _ = source_context
    responses[f"repos/{REPOSITORY}/compare/{SHA}...master"]["status"] = status
    with pytest.raises(ValueError, match="master history"):
        verify()


def test_source_rejects_unrelated_comparison_base(source_context):
    verify, _, responses, _ = source_context
    responses[f"repos/{REPOSITORY}/compare/{SHA}...master"]["base_commit"]["sha"] = "b" * 40
    with pytest.raises(ValueError, match="master history"):
        verify()


def test_api_pages_uses_read_only_paginated_github_requests(monkeypatch):
    commands = []
    monkeypatch.setattr(
        release_ci.subprocess,
        "check_output",
        lambda command, **kwargs: commands.append(command) or "[{}]",
    )
    assert release_ci.api_pages("repos/owner/app/actions/runs") == [{}]
    assert commands == [["gh", "api", "--paginate", "--slurp", "repos/owner/app/actions/runs"]]


def test_source_main_rechecks_tag_after_master_proof(monkeypatch):
    events = []
    monkeypatch.setattr(
        sys, "argv", ["gate", "--repository", REPOSITORY, "--sha", SHA, "--tag", TAG, "--source"]
    )
    monkeypatch.setattr(release_ci, "verify_release_source", lambda *args: events.append("tag"))
    monkeypatch.setattr(
        release_ci, "check_master_ci", lambda *args: events.append("master") or True
    )
    release_ci.main()
    assert events == ["tag", "master", "tag"]


def test_main_wait_is_bounded_and_does_not_publish_without_proof(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["gate", "--repository", REPOSITORY, "--sha", SHA, "--tag", TAG, "--timeout", "1"],
    )
    monkeypatch.setattr(release_ci, "check_release_ci", lambda *args: False)
    ticks = iter([0, 2])
    monkeypatch.setattr(release_ci.time, "monotonic", lambda: next(ticks))
    with pytest.raises(SystemExit, match="1"):
        release_ci.main()


def test_workflows_keep_full_master_ci_and_release_only_tag_jobs():
    ci = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    release = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    # PyYAML uses YAML 1.1 and treats the Actions 'on' key as a boolean.
    assert ci[True]["push"] == {"branches": ["master"]}
    assert release[True] == {"push": {"tags": ["v*"]}}
    assert {"python-unit", "python-integration", "reader-parity", "security", "required"} <= set(
        ci["jobs"]
    )
    assert "release-images" not in ci["jobs"]
    jobs = release["jobs"]
    assert set(jobs) == {"verified-source", "release-images", "required"}
    assert jobs["release-images"]["needs"] == "verified-source"
    assert "needs.verified-source.result == 'success'" in jobs["release-images"]["if"]
    assert "github.event_name == 'push' && github.ref_type == 'tag'" in jobs["release-images"]["if"]
    assert "github.repository == 'abhi1693/devfeed.tech'" in jobs["verified-source"]["if"]
    assert jobs["release-images"]["uses"] == "./.github/workflows/containers.yml"
    assert jobs["release-images"]["with"] == {"publish": True}
    assert release["concurrency"]["cancel-in-progress"] is False
    assert jobs["verified-source"]["permissions"] == {"contents": "read", "actions": "read"}
    assert jobs["verified-source"]["timeout-minutes"] == 65
    preflight = jobs["verified-source"]["steps"][-1]
    assert "--source" in preflight["run"] and "--timeout 3600" in preflight["run"]
    assert all("continue-on-error" not in job for job in jobs.values())


@pytest.mark.parametrize("dependency", ["verified-source", "release-images"])
@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", None])
def test_release_required_rejects_missing_or_unsuccessful_dependencies(dependency, result):
    jobs = yaml.safe_load((REPO_ROOT / ".github/workflows/release.yml").read_text())["jobs"]
    required = jobs["required"]
    assert set(required["needs"]) == {"verified-source", "release-images"}
    assert required["if"] == "always() && !cancelled()"
    results = {name: {"result": "success"} for name in required["needs"]}
    if result is None:
        del results[dependency]
    else:
        results[dependency]["result"] = result
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", required["steps"][0]["run"]],
        env={**os.environ, "RESULTS": json.dumps(results)},
        capture_output=True,
        text=True,
    )
    assert (completed.returncode == 0) is (result == "success")


def test_extension_gate_uses_checked_out_commit_without_local_tag(tmp_path, monkeypatch):
    # A linked worktree's exported git paths must not affect this disposable repo.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    workflow_text = (REPO_ROOT / ".github/workflows/extension-release.yml").read_text()
    assert "github.event_name == 'release' && github.sha" in workflow_text
    assert "format('refs/tags/{0}', inputs.release_tag)" in workflow_text
    script = textwrap.dedent(
        workflow_text.split("        run: |\n", 1)[1].split("  packages:\n", 1)[0]
    )
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
            "GITHUB_REPOSITORY": REPOSITORY,
            "RELEASE_TAG": TAG,
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "scripts/ci/require_release_ci.py",
        "--repository",
        REPOSITORY,
        "--sha",
        sha,
        "--tag",
        TAG,
        "--timeout",
        "5400",
    ]
