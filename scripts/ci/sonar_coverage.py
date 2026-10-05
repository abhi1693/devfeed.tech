"""Combine CI coverage and give SonarQube unambiguous repository-relative paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parents[2]
PYTHON_SUITES = ("python-unit", *(f"python-integration-{shard}" for shard in range(3)))
FRONTENDS = ("web", "admin")


def normalize_lcov(path: Path, root: Path, workspace: Path) -> None:
    """Resolve workspace paths, including shared packages, without basename collisions."""
    root = root.resolve()
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Coverage report is outside the repository: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    sources = 0
    for index, line in enumerate(lines):
        if not line.startswith("SF:"):
            continue
        filename = Path(line.removeprefix("SF:"))
        base = root if filename.parts and filename.parts[0] in {"apps", "packages"} else workspace
        source = (base / filename).resolve()
        if not source.is_relative_to(root):
            raise ValueError(f"Coverage source is outside the repository: {filename}")
        if not source.is_file():
            raise ValueError(f"Coverage source does not exist: {filename}")
        lines[index] = f"SF:{source.relative_to(root).as_posix()}"
        sources += 1
    if not sources or not any(line.startswith("DA:") for line in lines):
        raise ValueError(f"No line coverage in {path}")
    with path.open("w", encoding="utf-8") as report:
        report.write("\n".join(lines) + "\n")


def prepare_reports(
    root: Path = ROOT,
    *,
    browser_coverage: bool = False,
    dast_coverage: bool = False,
    mutation_coverage: bool = False,
) -> None:
    root = root.resolve()
    reports = root / "reports/coverage"
    databases = [reports / suite / "coverage.db" for suite in PYTHON_SUITES]
    lcov_reports = [reports / frontend / "lcov.info" for frontend in FRONTENDS]
    utilities = {
        "browser-budgets": browser_coverage,
        "dast": dast_coverage,
        "mutation": mutation_coverage,
    }
    lcov_reports.extend(
        reports / suite / "lcov.info" for suite, required in utilities.items() if required
    )
    if dast_coverage:
        databases.append(reports / "dast/coverage.db")
    # A partial download must fail rather than silently publish incomplete coverage.
    for path in [*databases, *lcov_reports]:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Missing or empty coverage report: {path}")
    for frontend in FRONTENDS:
        normalize_lcov(reports / frontend / "lcov.info", root, root / "apps" / frontend)
    for suite, required in utilities.items():
        if required:
            normalize_lcov(reports / suite / "lcov.info", root, root)

    coverage = Coverage(
        data_file=str(reports / "coverage.db"), config_file=str(root / "pyproject.toml")
    )
    coverage.combine(data_paths=[str(path) for path in databases], strict=True, keep=True)
    if not coverage.get_data().measured_files():
        raise ValueError("No Python coverage was measured")
    # Workspace roots are useful for the unit inventory, but XML must distinguish
    # identically named files across services and retain measured CI utilities.
    coverage.set_option("run:source", [str(root)])
    coverage.xml_report(outfile=str(reports / "python.xml"))


if __name__ == "__main__":
    try:
        prepare_reports(
            browser_coverage=os.environ.get("BROWSER_COVERAGE_REQUIRED") == "true",
            dast_coverage=os.environ.get("DAST_COVERAGE_REQUIRED") == "true",
            mutation_coverage=os.environ.get("MUTATION_COVERAGE_REQUIRED") == "true",
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
