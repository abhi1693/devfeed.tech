"""Balance selected whole test files; each CI job owns its disposable services."""

from collections import Counter

import pytest


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
