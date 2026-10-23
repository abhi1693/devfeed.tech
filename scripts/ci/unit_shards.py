"""Validate a complete unit-test partition before merging coverage and JUnit reports."""

import argparse
import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException

from scripts.ci.check_reports import check_junit
from scripts.ci.pytest_shard import run_identity, suite_fingerprint


def read_manifest(directory: Path, index: int, count: int) -> dict:
    document = json.loads((directory / "manifest.json").read_text())
    if (
        document.get("schema") != 1
        or document.get("run") != run_identity()
        or type(document.get("index")) is not int
        or document["index"] != index
        or type(document.get("count")) is not int
        or document["count"] != count
        or type(document.get("exit_code")) is not int
        or document["exit_code"] != 0
    ):
        raise ValueError(f"Unit shard {index} failed or belongs to another run/partition")
    selected = document.get("selected")
    if (
        type(document.get("collected")) is not int
        or document["collected"] < 1
        or not isinstance(document.get("fingerprint"), str)
        or not isinstance(selected, list)
        or not selected
        or any(not isinstance(nodeid, str) or not nodeid for nodeid in selected)
    ):
        raise ValueError(f"Unit shard {index} has no valid collection manifest")
    return document


def read_junit(root: Path, index: int, selected_count: int) -> list[ET.Element]:
    report = root / "reports" / f"python-unit-{index}.xml"
    check_junit(report)
    tree = ET.parse(report).getroot()
    if len(list(tree.iter("testcase"))) != selected_count:
        raise ValueError(f"Unit shard {index} did not execute every selected test")
    suites = [tree] if tree.tag == "testsuite" else tree.findall("testsuite")
    return [copy.deepcopy(suite) for suite in suites]


def read_coverage(directory: Path, index: int) -> str:
    database = directory / "coverage.db"
    if not database.is_file() or not database.stat().st_size:
        raise ValueError(f"Unit shard {index} has no coverage database")
    data = CoverageData(str(database))
    try:
        data.read()
    except CoverageException as exc:
        raise ValueError(f"Unit shard {index} has an invalid coverage database") from exc
    if not data.has_arcs() or not any(data.lines(path) for path in data.measured_files()):
        raise ValueError(f"Unit shard {index} has no measured branch coverage")
    return str(database)


def merge(root: Path, count: int) -> None:
    if type(count) is not int or count < 1:
        raise ValueError("Unit shard count must be positive")
    coverage_root = root / "reports/coverage"
    expected = {f"python-unit-{index}" for index in range(count)}
    actual = {path.name for path in coverage_root.glob("python-unit-*")}
    if actual != expected:
        raise ValueError("Missing or unexpected unit shard directories")
    nodeids = []
    databases = []
    combined_junit = ET.Element("testsuites")
    collection = None
    for index in range(count):
        directory = coverage_root / f"python-unit-{index}"
        document = read_manifest(directory, index, count)
        current = document["collected"], document["fingerprint"]
        if collection is None:
            collection = current
        elif collection != current:
            raise ValueError("Unit shards selected different complete test suites")
        selected = document["selected"]
        nodeids.extend(selected)
        combined_junit.extend(read_junit(root, index, len(selected)))
        databases.append(read_coverage(directory, index))
    if len(nodeids) != len(set(nodeids)):
        raise ValueError("Unit shard test selections overlap")
    if collection != (len(nodeids), suite_fingerprint(nodeids)):
        raise ValueError("Unit shards did not execute the complete selected test suite")
    destination = coverage_root / "python-unit/coverage.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    measurement = Coverage(data_file=str(destination), config_file=str(root / "pyproject.toml"))
    measurement.erase()
    measurement.combine(data_paths=databases, strict=True, keep=True)
    measurement.save()
    ET.ElementTree(combined_junit).write(root / "reports/python-unit.xml", encoding="unicode")
    print(f"Merged {count} complete unit shards: {len(nodeids)} tests")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    try:
        merge(args.root, args.count)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
