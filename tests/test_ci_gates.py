"""Regression checks for the release pipeline's fail-closed report gates."""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reports = load_script("check_reports")
images = load_script("check_image_index")


def run_shard(directory, index, count):
    report = directory / f"shard-{index}.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.ci.pytest_shard",
            "-c",
            str(directory / "pytest.ini"),
            str(directory),
            "-m",
            "integration",
            "--ci-shard-index",
            str(index),
            "--ci-shard-count",
            str(count),
            f"--junitxml={report}",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
    )
    return result, report


def test_ci_shards_cover_every_selected_test_once_and_keep_files_together(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = integration\n")
    for number in range(12):
        (tmp_path / f"test_sample_{number}.py").write_text(
            "import pytest\n"
            "@pytest.mark.integration\n"
            "@pytest.mark.parametrize('value', [1, 2])\n"
            "def test_selected(value): assert value > 0\n"
            "def test_unit(): raise AssertionError('must be deselected')\n"
        )
    all_cases = set()
    all_files = set()
    for index in range(3):
        result, report = run_shard(tmp_path, index, 3)
        assert result.returncode == 0, result.stdout + result.stderr
        reports.check_junit(report)
        cases = list(ET.parse(report).getroot().iter("testcase"))
        identities = {(case.get("classname"), case.get("name")) for case in cases}
        files = {case.get("classname") for case in cases}
        assert not all_cases & identities
        assert not all_files & files
        all_cases |= identities
        all_files |= files
    assert len(all_cases) == 24
    assert len(all_files) == 12
    result, report = run_shard(tmp_path, 0, 1)
    assert result.returncode == 0, result.stdout + result.stderr
    unsharded = {
        (case.get("classname"), case.get("name"))
        for case in ET.parse(report).getroot().iter("testcase")
    }
    assert all_cases == unsharded


@pytest.mark.parametrize("index,count", [(-1, 3), (3, 3), (0, 0)])
def test_ci_shards_reject_invalid_partition(tmp_path, index, count):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    result, _ = run_shard(tmp_path, index, count)
    assert result.returncode == 4
    assert "Require 0 <= ci-shard-index < ci-shard-count" in result.stderr


@pytest.mark.parametrize(
    "event,ref_type",
    [
        ("pull_request", "branch"),
        ("push", "branch"),
        ("push", "tag"),
        ("workflow_dispatch", "tag"),
        ("merge_group", "branch"),
        ("schedule", "branch"),
    ],
)
def test_ci_required_accepts_only_the_expected_successes(event, ref_type):
    # Execute the actual gate, including the mutually exclusive release/check jobs.
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    gate = textwrap.dedent(workflow.split("        run: |\n", 1)[1])
    release = event == "push" and ref_type == "tag"
    results = {
        "python-unit": {"result": "success"},
        "admin-web": {"result": "success"},
        "user-web": {"result": "success"},
        "extensions": {"result": "success"},
        "python-integration": {"result": "success"},
        "reader-parity": {"result": "success"},
        "security": {"result": "success"},
        "containers": {"result": "skipped" if release else "success"},
        "release-images": {"result": "success" if release else "skipped"},
        "performance": {"result": "success" if event == "pull_request" else "skipped"},
    }

    def evaluate():
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", gate],
            env={
                **os.environ,
                "EVENT_NAME": event,
                "REF_TYPE": ref_type,
                "RESULTS": json.dumps(results),
            },
            capture_output=True,
            text=True,
        ).returncode

    assert evaluate() == 0
    for job in results:
        expected = results[job]["result"]
        for unexpected in {"success", "skipped", "failure", "cancelled"} - {expected}:
            results[job]["result"] = unexpected
            assert evaluate() != 0, (event, ref_type, job, unexpected)
        results[job]["result"] = expected


@pytest.mark.parametrize("status", ["failure", "error", "skipped"])
def test_junit_rejects_unsuccessful_tests(tmp_path, status):
    path = tmp_path / "test.xml"
    path.write_text(f"<testsuite><testcase><{status}/></testcase></testsuite>")
    with pytest.raises(ValueError):
        reports.check_junit(path)


def test_junit_rejects_empty_suite(tmp_path):
    path = tmp_path / "test.xml"
    path.write_text('<testsuite tests="100"/>')
    with pytest.raises(ValueError):
        reports.check_junit(path)


def test_junit_accepts_nested_passing_suites(tmp_path):
    path = tmp_path / "test.xml"
    path.write_text("<testsuites><testsuite><testcase/></testsuite></testsuites>")
    reports.check_junit(path)


def test_sarif_rejects_missing_reports(tmp_path):
    with pytest.raises(ValueError):
        reports.check_sarif(tmp_path)


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [{"results": [{"ruleId": "py/security-issue"}]}],
        [{"results": [], "invocations": [{"executionSuccessful": False}]}],
    ],
)
def test_sarif_rejects_findings_and_incomplete_analysis(tmp_path, runs):
    (tmp_path / "python.sarif").write_text(json.dumps({"runs": runs}))
    with pytest.raises(ValueError):
        reports.check_sarif(tmp_path)


def test_sarif_accepts_clean_analysis(tmp_path):
    (tmp_path / "python.sarif").write_text(json.dumps({"runs": [{"results": []}]}))
    reports.check_sarif(tmp_path)


def index(architectures):
    return {
        "manifests": [{"platform": {"os": "linux", "architecture": arch}} for arch in architectures]
    }


@pytest.mark.parametrize(
    "architectures", [[], ["amd64"], ["arm64", "arm64"], ["amd64", "arm64"], ["arm64", "386"]]
)
def test_image_index_rejects_missing_duplicate_or_extra_platforms(architectures):
    with pytest.raises(ValueError):
        images.check_index(index(architectures))


def test_image_index_accepts_arm64_with_buildkit_attestation():
    document = index(["arm64"])
    document["manifests"].append(
        {
            "platform": {"os": "unknown", "architecture": "unknown"},
            "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
        }
    )
    images.check_index(document)
