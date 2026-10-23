"""Balance selected whole test files; each CI job owns its disposable services."""

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest

MANIFEST = pytest.StashKey[dict]()


def suite_fingerprint(nodeids):
    return hashlib.sha256(json.dumps(sorted(nodeids)).encode()).hexdigest()


def run_identity():
    return {
        key: os.environ.get(key, "")
        for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")
    }


def balanced_shards(paths, count):
    """Place files with most selected cases first; break ties by path and bin index."""
    weights = Counter(paths)
    loads = [0] * count
    assignments = {}
    for path in sorted(weights, key=lambda path: (-weights[path], path)):
        shard = min(range(count), key=lambda index: (loads[index], index))
        assignments[path] = shard
        loads[shard] += weights[path]
    return assignments


def pytest_addoption(parser):
    group = parser.getgroup("ci sharding")
    group.addoption("--ci-shard-index", type=int, default=0)
    group.addoption("--ci-shard-count", type=int, default=1)
    group.addoption("--ci-shard-report", type=Path)


def pytest_configure(config):
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    if not 0 <= index < count:
        raise pytest.UsageError("Require 0 <= ci-shard-index < ci-shard-count")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # Resume after pytest's -m/-k deselection. Every runner plans from the same
    # selected cases, without a generated timing snapshot or file allowlist.
    yield
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    complete = [item.nodeid for item in items]
    paths = [item.path.relative_to(config.rootpath).as_posix() for item in items]
    assignments = balanced_shards(paths, count)
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item, path in zip(items, paths, strict=True):
        # Keep module fixtures, cases and all parametrizations in one process.
        shard = assignments[path]
        (selected if shard == index else deselected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
    config.stash[MANIFEST] = {
        "schema": 1,
        "run": run_identity(),
        "index": index,
        "count": count,
        "collected": len(complete),
        "fingerprint": suite_fingerprint(complete),
        "selected": [item.nodeid for item in selected],
    }


def pytest_sessionfinish(session, exitstatus):
    path = session.config.getoption("ci_shard_report")
    if path is None:
        return
    document = session.config.stash.get(MANIFEST, {})
    document["exit_code"] = int(exitstatus)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")
