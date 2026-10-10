"""Coverage import regressions for the Python shards and two frontend workspaces."""

import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "sonar_coverage", ROOT / "scripts/ci/sonar_coverage.py"
)
sonar_coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonar_coverage)
FRONTENDS = sonar_coverage.FRONTENDS
PYTHON_SUITES = sonar_coverage.PYTHON_SUITES
normalize_lcov = sonar_coverage.normalize_lcov
prepare_reports = sonar_coverage.prepare_reports


@pytest.mark.parametrize(
    ("source", "expected_test"),
    [
        ("scripts/testing/dev-card-promo.mjs", True),
        ("scripts/testing/preview-cover.mjs", True),
        ("scripts/testing/nonce-csp.mjs", True),
        ("scripts/testing/sign-in.py", True),
        ("apps/web/tests/date-format.test.ts", True),
        ("scripts/testing/dast-browser.mjs", False),
        ("scripts/testing/dast-selectors.mjs", False),
        ("scripts/testing/live-browser.mjs", False),
        ("scripts/ci/api-performance-changes.mjs", False),
        ("apps/web/src/components/feed-filters.tsx", False),
        ("packages/ui/src/date-format.ts", False),
    ],
)
def test_sonar_keeps_browser_helpers_as_tests_and_runtime_helpers_as_source(source, expected_test):
    contents = (ROOT / "sonar-project.properties").read_text().replace("\\\n", "")
    properties = dict(
        line.split("=", 1) for line in contents.splitlines() if line and not line.startswith("#")
    )

    def matches(key):
        return any(
            fnmatchcase(source, pattern.strip())
            for pattern in properties.get(key, "").split(",")
            if pattern.strip()
        )

    path = Path(source)
    assert (ROOT / path).is_file()
    in_test_root = any(path.is_relative_to(root) for root in properties["sonar.tests"].split(","))
    is_test = (
        in_test_root and matches("sonar.test.inclusions") and not matches("sonar.test.exclusions")
    )
    assert is_test == expected_test
    in_source_root = any(
        path.is_relative_to(root) for root in properties["sonar.sources"].split(",")
    )
    # Sonar also applies test-inclusion patterns as source exclusions, even
    # when test exclusions remove a matching file from the test scope.
    is_source = (
        in_source_root and not matches("sonar.exclusions") and not matches("sonar.test.inclusions")
    )
    assert is_source == (not expected_test)
    assert not matches("sonar.coverage.exclusions")


def write_lcov(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"TN:\nSF:{source}\nDA:1,1\nLF:1\nLH:1\nend_of_record\n")


def test_lcov_distinguishes_app_files_and_resolves_shared_sources(tmp_path):
    for frontend in FRONTENDS:
        workspace = tmp_path / "apps" / frontend
        (workspace / "src").mkdir(parents=True)
        (workspace / "src/page.ts").write_text("export const page = 1;\n")
        report = tmp_path / f"{frontend}.info"
        write_lcov(report, "src/page.ts")
        normalize_lcov(report, tmp_path, workspace)
        normalize_lcov(report, tmp_path, workspace)
        assert f"SF:apps/{frontend}/src/page.ts\n" in report.read_text()
        assert "DA:1,1\nLF:1\nLH:1\nend_of_record\n" in report.read_text()
    shared = tmp_path / "packages/ui/src/card.ts"
    shared.parent.mkdir(parents=True)
    shared.write_text("export const card = 1;\n")
    write_lcov(report, "../../packages/ui/src/card.ts")
    normalize_lcov(report, tmp_path, tmp_path / "apps/web")
    assert "SF:packages/ui/src/card.ts\n" in report.read_text()


def test_lcov_combines_workspace_and_repository_ci_helper_paths(tmp_path):
    workspace = tmp_path / "apps/web"
    app_source = workspace / "src/page.ts"
    helper = tmp_path / "scripts/ci/api-performance-changes.mjs"
    for source in (app_source, helper):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("export const value = 1;\n")
    report = tmp_path / "reports/coverage/web/lcov.info"
    write_lcov(report, "src/page.ts")
    report.write_text(
        report.read_text()
        + "SF:scripts/ci/api-performance-changes.mjs\nDA:1,2\nLF:1\nLH:1\nend_of_record\n"
    )
    normalize_lcov(report, tmp_path, workspace)
    normalize_lcov(report, tmp_path, workspace)
    assert "SF:apps/web/src/page.ts\n" in report.read_text()
    assert "SF:scripts/ci/api-performance-changes.mjs\nDA:1,2\n" in report.read_text()


@pytest.mark.parametrize("source", ["../../../outside.ts", "src/missing.ts"])
def test_lcov_rejects_unresolvable_sources(tmp_path, source):
    report = tmp_path / "lcov.info"
    write_lcov(report, source)
    with pytest.raises(ValueError, match="outside the repository|does not exist"):
        normalize_lcov(report, tmp_path, tmp_path / "apps/web")


def test_lcov_rejects_empty_measurements(tmp_path):
    report = tmp_path / "lcov.info"
    report.write_text("TN:\nend_of_record\n")
    with pytest.raises(ValueError, match="No line coverage"):
        normalize_lcov(report, tmp_path, tmp_path / "apps/web")


@pytest.mark.parametrize("symlink", [False, True])
def test_lcov_rejects_reports_outside_the_repository(tmp_path, symlink):
    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside.info"
    write_lcov(outside, "src/page.ts")
    original = outside.read_text()
    report = outside
    if symlink:
        report = root / "lcov.info"
        report.symlink_to(outside)
    with pytest.raises(ValueError, match="Coverage report is outside the repository"):
        normalize_lcov(report, root, root / "apps/web")
    assert outside.read_text() == original


def test_prepare_rejects_missing_shards(tmp_path):
    with pytest.raises(ValueError, match="Missing or empty coverage report"):
        prepare_reports(tmp_path)


@pytest.mark.parametrize("empty", [False, True])
def test_prepare_requires_browser_coverage_when_the_job_ran(tmp_path, empty):
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"coverage placeholder")
    for frontend in FRONTENDS:
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    if empty:
        path = tmp_path / "reports/coverage/browser-budgets/lcov.info"
        path.parent.mkdir(parents=True)
        path.touch()
    with pytest.raises(ValueError, match="Missing or empty coverage report.*browser-budgets"):
        prepare_reports(tmp_path, browser_coverage=True)


def test_lcov_resolves_root_ci_helpers(tmp_path):
    source = tmp_path / "scripts/ci/browser-performance.mjs"
    source.parent.mkdir(parents=True)
    source.write_text("export const measured = true;\n")
    report = tmp_path / "reports/coverage/browser-budgets/lcov.info"
    write_lcov(report, str(source))
    normalize_lcov(report, tmp_path, tmp_path)
    assert "SF:scripts/ci/browser-performance.mjs\n" in report.read_text()


@pytest.mark.parametrize("backend_roots", [False, True])
@pytest.mark.parametrize("browser_coverage", [False, True])
@pytest.mark.parametrize("dast_coverage", [False, True])
@pytest.mark.parametrize("mutation_coverage", [False, True])
@pytest.mark.parametrize("property_coverage", [False, True])
@pytest.mark.parametrize("recovery_coverage", [False, True])
def test_prepare_combines_complementary_shard_coverage(
    tmp_path,
    backend_roots,
    browser_coverage,
    dast_coverage,
    mutation_coverage,
    property_coverage,
    recovery_coverage,
):
    config = "[tool.coverage.run]\nrelative_files = true\n"
    if backend_roots:
        config += 'source = ["apps/api/src", "apps/user-api/src"]\n'
    (tmp_path / "pyproject.toml").write_text(config)
    source = tmp_path / "apps/api/src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("def choose(value):\n    if value:\n        return 1\n    return 0\n")
    additional = ("apps/user-api/src/main.py", "scripts/ci/helper.py")
    for filename in additional:
        source = tmp_path / filename
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n")
    arcs = [
        {(-1, 1), (1, -1)},
        {(-1, 2), (2, 3), (3, -1)},
        {(-1, 2), (2, 4), (4, -1)},
        {(-1, 1), (1, -1)},
    ]
    for suite, covered in zip(PYTHON_SUITES, arcs, strict=True):
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        data = CoverageData(basename=str(path))
        measured = {"apps/api/src/main.py": covered}
        if suite == "python-unit":
            measured.update({filename: {(-1, 1), (1, -1)} for filename in additional})
        data.add_arcs(measured)
        data.write()
    for frontend in FRONTENDS:
        source = tmp_path / "apps" / frontend / "src/page.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const page = 1;\n")
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    browser_report = tmp_path / "reports/coverage/browser-budgets/lcov.info"
    if browser_coverage:
        source = tmp_path / "scripts/ci/browser-performance.mjs"
        source.write_text("export const measured = true;\n")
        write_lcov(browser_report, str(source))
    if dast_coverage:
        source = tmp_path / "scripts/ci/dast.py"
        source.write_text("def scan():\n    return 1\n")
        path = tmp_path / "reports/coverage/dast/coverage.db"
        path.parent.mkdir(parents=True)
        data = CoverageData(basename=str(path))
        data.add_arcs({"scripts/ci/dast.py": {(-1, 1), (1, -1), (-1, 2), (2, -1)}})
        data.write()
        source = tmp_path / "scripts/testing/dast-browser.mjs"
        source.parent.mkdir(parents=True)
        source.write_text("export const authenticated = true;\n")
        write_lcov(tmp_path / "reports/coverage/dast/lcov.info", str(source))
    if mutation_coverage:
        source = tmp_path / "scripts/ci/mutation.mjs"
        source.write_text("export const measured = true;\n")
        write_lcov(tmp_path / "reports/coverage/mutation/lcov.info", str(source))
    if property_coverage:
        source = tmp_path / "scripts/ci/property-tests.mjs"
        source.write_text("export const measured = true;\n")
        write_lcov(tmp_path / "reports/coverage/property/lcov.info", str(source))
    if recovery_coverage:
        source = tmp_path / "scripts/ci/failure_recovery.py"
        source.write_text("def recover():\n    return 1\n")
        path = tmp_path / "reports/coverage/recovery/coverage.db"
        path.parent.mkdir(parents=True)
        data = CoverageData(basename=str(path))
        data.add_arcs({"scripts/ci/failure_recovery.py": {(-1, 1), (1, -1), (-1, 2), (2, -1)}})
        data.write()
    # Coverage.py configures process-wide path handling. Keep this fixture's
    # repository separate from the coverage measuring the real test suite.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; from pathlib import Path; "
            "runpy.run_path(sys.argv[1])['prepare_reports'](Path.cwd(), "
            "browser_coverage=sys.argv[2] == '1', dast_coverage=sys.argv[3] == '1', "
            "mutation_coverage=sys.argv[4] == '1', property_coverage=sys.argv[5] == '1', "
            "recovery_coverage=sys.argv[6] == '1')",
            str(ROOT / "scripts/ci/sonar_coverage.py"),
            "1" if browser_coverage else "0",
            "1" if dast_coverage else "0",
            "1" if mutation_coverage else "0",
            "1" if property_coverage else "0",
            "1" if recovery_coverage else "0",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    report = ET.parse(tmp_path / "reports/coverage/python.xml")
    classes = {entry.get("filename"): entry for entry in report.findall(".//class")}
    assert set(classes) == {"apps/api/src/main.py", *additional} | (
        {"scripts/ci/dast.py"} if dast_coverage else set()
    ) | ({"scripts/ci/failure_recovery.py"} if recovery_coverage else set())
    if recovery_coverage:
        assert (
            classes["scripts/ci/failure_recovery.py"].find("./lines/line[@number='2']").get("hits")
            == "1"
        )
    if dast_coverage:
        assert classes["scripts/ci/dast.py"].find("./lines/line[@number='2']").get("hits") == "1"
        assert (
            "SF:scripts/testing/dast-browser.mjs\n"
            in (tmp_path / "reports/coverage/dast/lcov.info").read_text()
        )
    measured = classes["apps/api/src/main.py"]
    assert {line.get("number") for line in measured.findall("./lines/line")} == {"1", "2", "3", "4"}
    assert all(line.get("hits") == "1" for line in measured.findall("./lines/line"))
    assert measured.find("./lines/line[@number='2']").get("condition-coverage") == "100% (2/2)"
    assert all(
        (tmp_path / "reports/coverage" / suite / "coverage.db").is_file() for suite in PYTHON_SUITES
    )
    if browser_coverage:
        assert "SF:scripts/ci/browser-performance.mjs\n" in browser_report.read_text()
    if mutation_coverage:
        assert (
            "SF:scripts/ci/mutation.mjs\n"
            in (tmp_path / "reports/coverage/mutation/lcov.info").read_text()
        )

    if property_coverage:
        assert (
            "SF:scripts/ci/property-tests.mjs\n"
            in (tmp_path / "reports/coverage/property/lcov.info").read_text()
        )


def test_prepare_imports_absolute_sources_outside_the_current_working_directory(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nrelative_files = true\n")
    source = tmp_path / "apps/api/src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        data = CoverageData(basename=str(path))
        data.add_arcs({str(source): {(-1, 1), (1, -1)}})
        data.write()
    for frontend in FRONTENDS:
        page = tmp_path / "apps" / frontend / "src/page.ts"
        page.parent.mkdir(parents=True)
        page.write_text("export const page = 1;\n")
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")

    prepare_reports(tmp_path)

    report = ET.parse(tmp_path / "reports/coverage/python.xml")
    measured = report.find(".//class")
    assert measured.get("filename") == "apps/api/src/main.py"
    assert measured.find("./lines/line").attrib == {"number": "1", "hits": "1"}


@pytest.mark.parametrize("missing", ["coverage.db", "lcov.info"])
@pytest.mark.parametrize("empty", [False, True])
def test_prepare_requires_both_dast_reports_when_scan_ran(tmp_path, missing, empty):
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"coverage placeholder")
    for frontend in FRONTENDS:
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    for filename in ["coverage.db", "lcov.info"]:
        path = tmp_path / "reports/coverage/dast" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if filename != missing:
            path.write_bytes(b"placeholder")
        elif empty:
            path.touch()
    with pytest.raises(ValueError, match="Missing or empty coverage report.*dast"):
        prepare_reports(tmp_path, dast_coverage=True)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("suite", ["mutation", "property"])
def test_prepare_requires_utility_coverage_when_the_job_ran(tmp_path, empty, suite):
    for python_suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / python_suite / "coverage.db"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"coverage placeholder")
    for frontend in FRONTENDS:
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    if empty:
        path = tmp_path / "reports/coverage" / suite / "lcov.info"
        path.parent.mkdir(parents=True)
        path.touch()
    with pytest.raises(ValueError, match=f"Missing or empty coverage report.*{suite}"):
        prepare_reports(tmp_path, **{f"{suite}_coverage": True})


@pytest.mark.parametrize("empty", [False, True])
def test_prepare_requires_recovery_measurements_when_the_job_ran(tmp_path, empty):
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"coverage placeholder")
    for frontend in FRONTENDS:
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    if empty:
        path = tmp_path / "reports/coverage/recovery/coverage.db"
        path.parent.mkdir(parents=True)
        path.touch()
    with pytest.raises(ValueError, match="Missing or empty coverage report.*recovery"):
        prepare_reports(tmp_path, recovery_coverage=True)
