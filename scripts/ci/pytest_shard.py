"""Balance selected whole test files; each CI job owns its disposable services."""

import json
import math
import re
from pathlib import Path, PurePosixPath

import pytest

PROFILE = Path(__file__).with_name("integration-durations.json")
DURATIONS = pytest.StashKey[tuple[dict[str, float], float]]()


def load_durations(path):
    """Validate the measured baseline without treating it as a test allowlist."""
    try:
        profile = json.loads(path.read_text())
        if (
            not isinstance(profile, dict)
            or type(profile.get("version")) is not int
            or profile["version"] != 1
        ):
            raise ValueError("Unsupported profile version")
        source = profile["source"]
        if (
            not isinstance(source, dict)
            or not re.fullmatch(r"[a-f0-9]{40}", source["head"])
            or type(source["run_id"]) is not int
            or source["run_id"] <= 0
            or type(source["test_cases"]) is not int
            or type(source["files"]) is not int
            or not isinstance(source["artifacts"], dict)
            or not source["artifacts"]
            or any(type(value) is not int or value <= 0 for value in source["artifacts"].values())
        ):
            raise ValueError("Invalid profile provenance")
        fallback = profile["fallback_seconds"]
        files = profile["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError("No measured files")
        weights = {}
        cases = 0
        for filename, measurement in files.items():
            name = PurePosixPath(filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or name.as_posix() != filename
                or name.suffix != ".py"
                or not isinstance(measurement, dict)
                or type(measurement["cases"]) is not int
                or measurement["cases"] <= 0
            ):
                raise ValueError("Invalid measured file")
            weights[filename] = measurement["seconds"]
            cases += measurement["cases"]
        if source["test_cases"] != cases or source["files"] != len(files):
            raise ValueError("Profile counts do not match measured files")
        if any(
            type(value) not in (float, int) or not math.isfinite(value) or value <= 0
            for value in [fallback, *weights.values()]
        ):
            raise ValueError("Durations must be finite positive seconds")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise pytest.UsageError(f"Invalid CI duration profile {path}: {error}") from error
    return weights, fallback


def balanced_shards(paths, weights, fallback, count):
    """Place longest files first; path and bin-index ties are deterministic."""
    loads = [0.0] * count
    assignments = {}
    for path in sorted(set(paths), key=lambda path: (-weights.get(path, fallback), path)):
        shard = min(range(count), key=lambda index: (loads[index], index))
        assignments[path] = shard
        loads[shard] += weights.get(path, fallback)
    return assignments


def pytest_addoption(parser):
    group = parser.getgroup("ci sharding")
    group.addoption("--ci-shard-index", type=int, default=0)
    group.addoption("--ci-shard-count", type=int, default=1)


def pytest_configure(config):
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    if not 0 <= index < count:
        raise pytest.UsageError("Require 0 <= ci-shard-index < ci-shard-count")
    config.stash[DURATIONS] = load_durations(PROFILE)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # Resume after pytest's -m/-k deselection. Every runner plans from the same
    # selected files, including files absent from the measured baseline.
    yield
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    weights, fallback = config.stash[DURATIONS]
    paths = [item.path.relative_to(config.rootpath).as_posix() for item in items]
    assignments = balanced_shards(paths, weights, fallback, count)
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item, path in zip(items, paths, strict=True):
        # Keep module fixtures, cases and all parametrizations in one process.
        shard = assignments[path]
        (selected if shard == index else deselected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
