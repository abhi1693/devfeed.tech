"""Coverage import regressions for the Python shards and two frontend workspaces."""

import importlib.util
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace

import pytest
from coverage import CoverageData

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT
spec = importlib.util.spec_from_file_location(
    "sonar_coverage", ROOT / "scripts/ci/sonar_coverage.py"
)
sonar_coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonar_coverage)
FRONTENDS = sonar_coverage.FRONTENDS
PYTHON_SUITES = sonar_coverage.PYTHON_SUITES
normalize_lcov = sonar_coverage.normalize_lcov
prepare_reports = sonar_coverage.prepare_reports


def sonar_properties():
    contents = (ROOT / "sonar-project.properties").read_text().replace("\\\n", "")
    return dict(
        line.split("=", 1) for line in contents.splitlines() if line and not line.startswith("#")
    )


def sonar_matches(properties, key, source):
    return any(
        fnmatchcase(source, pattern.strip())
        for pattern in properties.get(key, "").split(",")
        if pattern.strip()
    )


@pytest.mark.parametrize(
    ("source", "expected_test"),
    [
        ("scripts/testing/app-icons.mjs", True),
        ("scripts/testing/dev-card-promo.mjs", True),
        ("scripts/testing/preview-cover.mjs", True),
        ("scripts/testing/nonce-csp.mjs", True),
        ("scripts/testing/default-all.mjs", True),
        ("scripts/testing/sign-in.py", True),
        ("scripts/testing/login-recovery.mjs", True),
        ("scripts/testing/login-recovery.py", True),
        ("scripts/testing/tags.mjs", True),
        ("scripts/testing/featured-on.mjs", True),
        ("scripts/testing/server-reads.mjs", True),
        ("scripts/testing/user-mutations.mjs", True),
        ("apps/web/tests/date-format.test.ts", True),
        ("scripts/testing/adaptive_polling.py", False),
        ("scripts/testing/dast-browser.mjs", False),
        ("scripts/testing/dast-selectors.mjs", False),
        ("scripts/testing/live-browser.mjs", False),
        ("scripts/ci/api-performance-changes.mjs", False),
        ("scripts/ci/smoke-codex.cjs", True),
        ("scripts/ci/smoke-codex.sh", True),
        ("scripts/ci/smoke_fleet_contracts.py", True),
        ("scripts/ci/fleet_contracts/runtime.py", True),
        ("scripts/ci/fleet_contracts/frontend.cjs", True),
        ("scripts/ci/fleet_contracts/commands.json", True),
        ("scripts/ci/fixtures/codex/retention.cjs.fixture", True),
        ("infra/codex/server.cjs", False),
        ("infra/codex/transport.cjs", False),
        ("infra/codex/health.cjs", False),
        ("loadtests/test_histogram.py", True),
        ("loadtests/latency.py", False),
        ("loadtests/locustfile.py", False),
        ("apps/web/src/components/feed-filters.tsx", False),
        ("packages/ui/src/date-format.ts", False),
    ],
)
def test_sonar_keeps_browser_helpers_as_tests_and_runtime_helpers_as_source(source, expected_test):
    properties = sonar_properties()

    def matches(key):
        return sonar_matches(properties, key, source)

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


def test_every_testing_helper_has_an_explicit_fixture_or_operational_source_role():
    # These drivers run as measured CI utilities; fixtures and assertion helpers
    # belong to the test manifest instead. Keep the special source roles explicit.
    operational_sources = {
        "scripts/testing/adaptive_polling.py": "Measured deterministic polling workload",
        "scripts/testing/dast-browser.mjs": "Measured authenticated DAST browser driver",
        "scripts/testing/dast-selectors.mjs": "Measured DAST page-selection logic",
        "scripts/testing/live-browser.mjs": "Measured live browser login/profile driver",
    }
    properties = sonar_properties()
    helpers = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "scripts/testing").rglob("*")
        if path.is_file() and path.suffix in {".py", ".mjs"}
    }
    tests = {
        source for source in helpers if sonar_matches(properties, "sonar.test.inclusions", source)
    }
    assert tests.isdisjoint(operational_sources)
    assert helpers - tests == operational_sources.keys()
    assert all(operational_sources.values())
    for source in helpers:
        assert not sonar_matches(properties, "sonar.exclusions", source)
        assert not sonar_matches(properties, "sonar.coverage.exclusions", source)
        assert not sonar_matches(properties, "sonar.cpd.exclusions", source)


def test_generated_duplication_policy_uses_only_shared_generator_output_conventions():
    properties = sonar_properties()
    assert properties["sonar.cpd.exclusions"].split(",") == [
        "**/src/lib/api/generated/**",
        "**/*.generated.*",
    ]
    generated = [
        *ROOT.glob("apps/*/src/lib/api/generated/**/*.ts"),
        *ROOT.glob("packages/*/src/**/*.generated.ts"),
    ]
    assert generated
    for path in generated:
        source = path.relative_to(ROOT).as_posix()
        header = "\n".join(path.read_text().splitlines()[:5])
        assert "generated" in header.lower(), source
        assert sonar_matches(properties, "sonar.cpd.exclusions", source)
        assert not sonar_matches(properties, "sonar.coverage.exclusions", source)
        # The pre-existing admin client exclusion is retained. Other generated
        # reader/partner descriptors remain available for static security checks.
        if not source.startswith("apps/admin/"):
            assert not sonar_matches(properties, "sonar.exclusions", source)


@pytest.mark.parametrize(
    "source",
    [
        "apps/web/src/components/tags-content.tsx",
        "apps/web/src/lib/api.ts",
        "apps/web/src/lib/server/gateway.ts",
        "packages/telemetry/src/propagation.ts",
        "migrations/versions/0026_user_tag_follows.py",
        "scripts/generate_reader_routes.mjs",
        "scripts/ci/sonar_coverage.py",
        "scripts/ci/unit_shards.py",
        "scripts/ci/coverage_report.py",
        "scripts/ci/api_contracts.py",
        "packages/core/src/analytics_worker.py",
    ],
)
def test_handwritten_product_migration_and_generator_code_keeps_all_quality_checks(source):
    assert (ROOT / source).is_file()
    properties = sonar_properties()
    for parameter in (
        "sonar.test.inclusions",
        "sonar.exclusions",
        "sonar.coverage.exclusions",
        "sonar.cpd.exclusions",
    ):
        assert not sonar_matches(properties, parameter, source), parameter


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
def test_prepare_requires_real_loadtest_contract_coverage(tmp_path, empty):
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        if suite != "python-loadtest":
            path.write_bytes(b"coverage placeholder")
        elif empty:
            path.touch()
    for frontend in FRONTENDS:
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    with pytest.raises(ValueError, match="Missing or empty coverage report.*python-loadtest"):
        prepare_reports(tmp_path)


@pytest.mark.parametrize("shard", range(6))
@pytest.mark.parametrize("empty", [False, True])
def test_prepare_requires_every_one_of_the_six_integration_databases(tmp_path, shard, empty):
    assert (
        "python-unit",
        *(f"python-integration-{i}" for i in range(6)),
        "python-loadtest",
    ) == PYTHON_SUITES
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        if suite != f"python-integration-{shard}":
            path.write_bytes(b"coverage placeholder")
        elif empty:
            path.touch()
    for frontend in FRONTENDS:
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    with pytest.raises(
        ValueError, match=f"Missing or empty coverage report.*python-integration-{shard}"
    ):
        prepare_reports(tmp_path)


def test_lcov_resolves_root_ci_helpers(tmp_path):
    source = tmp_path / "scripts/ci/helper.mjs"
    source.parent.mkdir(parents=True)
    source.write_text("export const measured = true;\n")
    report = tmp_path / "reports/coverage/security/lcov.info"
    write_lcov(report, str(source))
    normalize_lcov(report, tmp_path, tmp_path)
    assert "SF:scripts/ci/helper.mjs\n" in report.read_text()


@pytest.mark.parametrize("backend_roots", [False, True])
@pytest.mark.parametrize("dast_coverage", [False, True])
@pytest.mark.parametrize("mutation_coverage", [False, True])
@pytest.mark.parametrize("property_coverage", [False, True])
@pytest.mark.parametrize("recovery_coverage", [False, True])
def test_prepare_combines_complementary_shard_coverage(
    tmp_path,
    backend_roots,
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
    ] + [{(-1, 1), (1, -1)}] * (len(PYTHON_SUITES) - 4)
    shard_sources = {
        suite: f"scripts/ci/shard_{index}.py" for index, suite in enumerate(PYTHON_SUITES)
    }
    for suite, covered in zip(PYTHON_SUITES, arcs, strict=True):
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        data = CoverageData(basename=str(path))
        measured = {"apps/api/src/main.py": covered}
        exclusive_source = shard_sources[suite]
        (tmp_path / exclusive_source).write_text("value = 1\n")
        measured[exclusive_source] = {(-1, 1), (1, -1)}
        if suite == "python-unit":
            measured.update({filename: {(-1, 1), (1, -1)} for filename in additional})
        data.add_arcs(measured)
        data.write()
    for frontend in FRONTENDS:
        source = tmp_path / "apps" / frontend / "src/page.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const page = 1;\n")
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
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
            "dast_coverage=sys.argv[2] == '1', "
            "mutation_coverage=sys.argv[3] == '1', property_coverage=sys.argv[4] == '1', "
            "recovery_coverage=sys.argv[5] == '1')",
            str(ROOT / "scripts/ci/sonar_coverage.py"),
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
    assert set(classes) == {"apps/api/src/main.py", *additional, *shard_sources.values()} | (
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
    assert all(
        classes[filename].find("./lines/line[@number='1']").get("hits") == "1"
        for filename in shard_sources.values()
    )
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
@pytest.mark.parametrize("suite", ["mutation", "property", "security"])
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


def test_prepare_rejects_security_reports_without_line_measurements(tmp_path):
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"coverage placeholder")
    for frontend in FRONTENDS:
        source = tmp_path / "apps" / frontend / "src/page.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const page = 1;\n")
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    report = tmp_path / "reports/coverage/security/lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text("TN:\nend_of_record\n")

    with pytest.raises(ValueError, match="No line coverage.*security"):
        prepare_reports(tmp_path, security_coverage=True)


def test_prepare_imports_security_launcher_and_transport_coverage(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nrelative_files = true\n")
    source = tmp_path / "apps/api/src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n")
    for suite in PYTHON_SUITES:
        path = tmp_path / "reports/coverage" / suite / "coverage.db"
        path.parent.mkdir(parents=True)
        data = CoverageData(basename=str(path))
        data.add_arcs({"apps/api/src/main.py": {(-1, 1), (1, -1)}})
        data.write()
    for frontend in FRONTENDS:
        source = tmp_path / "apps" / frontend / "src/page.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const page = 1;\n")
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    records = []
    expected = []
    for filename, hits in (("server.cjs", 1), ("transport.cjs", 2)):
        source = tmp_path / "infra/codex" / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("module.exports = {};\n")
        measurement = f"DA:1,{hits}\nLF:1\nLH:1\nend_of_record\n"
        records.append(f"TN:\nSF:{source}\n{measurement}")
        expected.append(f"TN:\nSF:infra/codex/{filename}\n{measurement}")
    report = tmp_path / "reports/coverage/security/lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text("".join(records))
    # Keep Coverage.py's process-wide path handling separate from the real suite.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; from pathlib import Path; "
            "import sys; runpy.run_path(sys.argv[1])['prepare_reports']("
            "Path.cwd(), security_coverage=True)",
            str(ROOT / "scripts/ci/sonar_coverage.py"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert report.read_text() == "".join(expected)
    measured = ET.parse(tmp_path / "reports/coverage/python.xml").find(".//class")
    assert measured.get("filename") == "apps/api/src/main.py"
    assert measured.find("./lines/line").attrib == {"number": "1", "hits": "1"}
    assert all(
        (tmp_path / "reports/coverage" / suite / "coverage.db").is_file() for suite in PYTHON_SUITES
    )


def test_cli_requires_security_measurements_when_requested(monkeypatch, capsys):
    reports = ROOT / "reports/coverage"
    baseline = {reports / suite / "coverage.db" for suite in PYTHON_SUITES} | {
        reports / frontend / "lcov.info" for frontend in FRONTENDS
    }
    security_report = reports / "security/lcov.info"
    original_is_file = Path.is_file
    original_stat = Path.stat
    original_open = Path.open

    def is_file(path, *args, **kwargs):
        if path in baseline:
            return True
        if path == security_report:
            return False
        return original_is_file(path, *args, **kwargs)

    def stat(path, *args, **kwargs):
        if path in baseline:
            return SimpleNamespace(st_size=1)
        return original_stat(path, *args, **kwargs)

    def open_file(path, *args, **kwargs):
        if path in baseline:
            raise AssertionError(
                "CLI opened a legacy report before rejecting missing security coverage"
            )
        return original_open(path, *args, **kwargs)

    # The CLI must reject the absent security artifact before reading or writing
    # real repository reports. All unrelated filesystem operations remain real.
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "open", open_file)
    for suite in ("BROWSER", "DAST", "MUTATION", "PROPERTY", "RECOVERY"):
        monkeypatch.setenv(f"{suite}_COVERAGE_REQUIRED", "false")
    monkeypatch.setenv("SECURITY_COVERAGE_REQUIRED", "true")

    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(ROOT / "scripts/ci/sonar_coverage.py"), run_name="__main__")

    assert error.value.code == 1
    assert capsys.readouterr().err == f"Missing or empty coverage report: {security_report}\n"
