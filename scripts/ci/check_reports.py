"""Fail closed on missing/empty test reports, skipped tests, or CodeQL findings."""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def check_junit(path: Path) -> None:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases:
        raise ValueError(f"{path}: no tests executed")
    failed = [
        case
        for case in cases
        if any(tag in {"failure", "error", "skipped"} for tag in [child.tag for child in case])
    ]
    if failed:
        raise ValueError(f"{path}: {len(failed)} tests failed, errored, or skipped")
    print(f"{path}: {len(cases)} tests passed with no skips")


def check_sarif(directory: Path) -> None:
    paths = sorted(directory.glob("*.sarif"))
    if not paths:
        raise ValueError(f"{directory}: missing CodeQL SARIF reports")
    for path in paths:
        runs = json.loads(path.read_text())["runs"]
        if not runs:
            raise ValueError(f"{path}: missing analysis runs")
        for run in runs:
            for invocation in run.get("invocations", []):
                if not invocation.get("executionSuccessful", True):
                    raise ValueError(f"{path}: analysis did not complete successfully")
            results = run["results"]
            if results:
                # Print rule IDs only; findings can contain sensitive source snippets.
                rules = sorted({result.get("ruleId", "unknown") for result in results})
                raise ValueError(f"{path}: {len(results)} CodeQL findings: {', '.join(rules)}")
        print(f"{path}: no CodeQL findings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("format", choices=["junit", "sarif"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        (check_junit if args.format == "junit" else check_sarif)(args.path)
    except (ValueError, KeyError, OSError, ET.ParseError) as exc:
        parser.exit(1, f"{exc}\n")
