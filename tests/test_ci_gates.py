"""Regression checks for the release pipeline's fail-closed report gates."""

import importlib.util
import json
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
    "architectures", [[], ["amd64"], ["arm64"], ["amd64", "amd64"], ["amd64", "arm64", "386"]]
)
def test_image_index_rejects_missing_duplicate_or_extra_platforms(architectures):
    with pytest.raises(ValueError):
        images.check_index(index(architectures))


def test_image_index_accepts_both_platforms_with_buildkit_attestation():
    document = index(["arm64", "amd64"])
    document["manifests"].append(
        {
            "platform": {"os": "unknown", "architecture": "unknown"},
            "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
        }
    )
    images.check_index(document)
