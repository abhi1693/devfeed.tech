"""Combine CI coverage and give SonarQube unambiguous repository-relative paths."""

from __future__ import annotations

import sys
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parents[2]
PYTHON_SUITES = ("python-unit", *(f"python-integration-{shard}" for shard in range(3)))
FRONTENDS = ("web", "admin")


def normalize_lcov(path: Path, root: Path, workspace: Path) -> None:
    """Resolve workspace paths, including shared packages, without basename collisions."""
    lines = path.read_text().splitlines()
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
    path.write_text("\n".join(lines) + "\n")


def prepare_reports(root: Path = ROOT) -> None:
    root = root.resolve()
    reports = root / "reports/coverage"
    databases = [reports / suite / "coverage.db" for suite in PYTHON_SUITES]
    lcov_reports = [reports / frontend / "lcov.info" for frontend in FRONTENDS]
    # A partial download must fail rather than silently publish incomplete coverage.
    for path in [*databases, *lcov_reports]:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Missing or empty coverage report: {path}")
    for frontend, path in zip(FRONTENDS, lcov_reports, strict=True):
        normalize_lcov(path, root, root / "apps" / frontend)

    coverage = Coverage(
        data_file=str(reports / "coverage.db"), config_file=str(root / "pyproject.toml")
    )
    coverage.combine(data_paths=[str(path) for path in databases], strict=True, keep=True)
    if not coverage.get_data().measured_files():
        raise ValueError("No Python coverage was measured")
    coverage.xml_report(outfile=str(reports / "python.xml"))


if __name__ == "__main__":
    try:
        prepare_reports()
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
