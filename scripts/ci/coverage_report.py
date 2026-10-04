"""Validate complete backend unit coverage and produce a bounded PR reporting artifact."""

import argparse
import json
import math
import os
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

COUNTERS = ("covered_lines", "num_statements", "covered_branches", "num_branches")


def counts(value: dict) -> dict:
    result = {name: value[name] for name in COUNTERS}
    if any(type(number) is not int or number < 0 for number in result.values()):
        raise ValueError("Invalid coverage counts")
    if (
        result["covered_lines"] > result["num_statements"]
        or result["covered_branches"] > result["num_branches"]
    ):
        raise ValueError("Covered counts exceed measured code")
    return result


def percentages(value: dict) -> dict:
    lines, branches = value["num_statements"], value["num_branches"]
    return {
        **value,
        "line_percent": 100 * value["covered_lines"] / lines if lines else 100.0,
        "branch_percent": 100 * value["covered_branches"] / branches if branches else 100.0,
        "combined_percent": 100
        * (value["covered_lines"] + value["covered_branches"])
        / (lines + branches)
        if lines + branches
        else 100.0,
    }


def summarize(root: Path, test_exit_code: int) -> dict:
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    if attempt < 1:
        raise ValueError("Unit run attempt must be positive")
    config = tomllib.loads((root / "pyproject.toml").read_text())
    coverage = config["tool"]["coverage"]
    members = config["tool"]["uv"]["workspace"]["members"]
    sources = coverage["run"]["source"]
    if coverage["run"].get("branch") is not True or set(sources) != {
        f"{member}/src" for member in members
    }:
        raise ValueError("Coverage must include every backend workspace with branches")
    if (
        len(sources) != len(set(sources))
        or coverage["run"].get("omit")
        or coverage["report"].get("omit")
    ):
        raise ValueError("Coverage sources must not be duplicated or omitted")
    minimum = coverage["report"]["fail_under"]
    if type(minimum) not in (int, float) or not math.isfinite(minimum) or not 0 < minimum <= 100:
        raise ValueError("Coverage minimum must be a finite percentage")
    document = json.loads((root / "reports/coverage/unit.json").read_text())
    if document["meta"].get("branch_coverage") is not True:
        raise ValueError("Branch coverage was not measured")
    expected = {
        path.relative_to(root).as_posix()
        for source in sources
        for path in (root / source).rglob("*.py")
    }
    if not expected or set(document["files"]) != expected:
        raise ValueError("Coverage inventory differs from backend source files")
    packages = {member: dict.fromkeys(COUNTERS, 0) for member in sorted(members)}
    for filename, entry in document["files"].items():
        member = next(member for member in members if filename.startswith(f"{member}/src/"))
        for name, count in counts(entry["summary"]).items():
            packages[member][name] += count
    total = {name: sum(package[name] for package in packages.values()) for name in COUNTERS}
    if counts(document["totals"]) != total or total["num_statements"] == 0:
        raise ValueError("Coverage totals do not match measured source")
    cases = list(ET.parse(root / "reports/python-unit.xml").getroot().iter("testcase"))
    if not cases:
        raise ValueError("No unit tests were reported")
    failed = sum(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    )
    skipped = sum(case.find("skipped") is not None for case in cases)
    tests = {
        "total": len(cases),
        "failed": failed,
        "skipped": skipped,
        "passed": len(cases) - failed - skipped,
    }
    total = percentages(total)
    passed = (
        test_exit_code == 0
        and failed == 0
        and skipped == 0
        and round(total["combined_percent"], 2) >= minimum
    )
    return {
        "schema": 1,
        "valid": True,
        "status": "passed" if passed else "failed",
        "minimum": minimum,
        "unit_exit_code": test_exit_code,
        "unit_attempt": attempt,
        "source_files": len(expected),
        "totals": total,
        "packages": {member: percentages(value) for member, value in packages.items()},
        "tests": tests,
    }


def markdown(summary: dict) -> str:
    if not summary["valid"]:
        return (
            "### Backend unit coverage\n\nCoverage report is missing or invalid. "
            "Inspect the Python unit job logs.\n"
        )
    total, tests = summary["totals"], summary["tests"]
    rows = [
        "### Backend unit coverage",
        "",
        f"**{summary['status'].upper()}** — minimum combined coverage "
        f"**{summary['minimum']:.2f}%**.",
        "",
        f"Tests: {tests['passed']} passed, {tests['failed']} failed, {tests['skipped']} skipped. "
        f"Source files: {summary['source_files']}.",
        "",
        "| Workspace | Lines | Branches | Combined |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, value in [("All backend code", total), *summary["packages"].items()]:
        rows.append(
            f"| {name} | {value['line_percent']:.2f}% | {value['branch_percent']:.2f}% | "
            f"{value['combined_percent']:.2f}% |"
        )
    rows.extend(
        [
            "",
            "Unit coverage includes every backend workspace. "
            "PostgreSQL/Redis integration tests run separately.",
            "",
        ]
    )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--test-exit-code", type=int, required=True)
    args = parser.parse_args()
    try:
        summary = summarize(args.root, args.test_exit_code)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, StopIteration):
        summary = {"schema": 1, "valid": False, "status": "incomplete"}
    destination = args.root / "reports/coverage"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (destination / "summary.md").write_text(markdown(summary))
    print(markdown(summary))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
