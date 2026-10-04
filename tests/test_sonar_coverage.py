"""Coverage import regressions for the Python shards and two frontend workspaces."""

import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
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


def test_prepare_rejects_missing_shards(tmp_path):
    with pytest.raises(ValueError, match="Missing or empty coverage report"):
        prepare_reports(tmp_path)


def test_prepare_combines_complementary_shard_coverage(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nrelative_files = true\n")
    source = tmp_path / "apps/api/src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("def choose(value):\n    if value:\n        return 1\n    return 0\n")
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
        data.add_arcs({"apps/api/src/main.py": covered})
        data.write()
    for frontend in FRONTENDS:
        source = tmp_path / "apps" / frontend / "src/page.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const page = 1;\n")
        write_lcov(tmp_path / "reports/coverage" / frontend / "lcov.info", "src/page.ts")
    # Coverage.py configures process-wide path handling. Keep this fixture's
    # repository separate from the coverage measuring the real test suite.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; from pathlib import Path; "
            "runpy.run_path(sys.argv[1])['prepare_reports'](Path.cwd())",
            str(ROOT / "scripts/ci/sonar_coverage.py"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    report = ET.parse(tmp_path / "reports/coverage/python.xml")
    measured = report.find(".//class")
    assert measured.get("filename") == "apps/api/src/main.py"
    assert {line.get("number") for line in measured.findall("./lines/line")} == {"1", "2", "3", "4"}
    assert all(line.get("hits") == "1" for line in measured.findall("./lines/line"))
    assert measured.find("./lines/line[@number='2']").get("condition-coverage") == "100% (2/2)"
    assert all(
        (tmp_path / "reports/coverage" / suite / "coverage.db").is_file() for suite in PYTHON_SUITES
    )
