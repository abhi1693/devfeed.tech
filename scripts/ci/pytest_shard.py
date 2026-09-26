"""Partition collected tests by file; each CI job owns its disposable services."""

import hashlib

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("ci sharding")
    group.addoption("--ci-shard-index", type=int, default=0)
    group.addoption("--ci-shard-count", type=int, default=1)


def pytest_configure(config):
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    if not 0 <= index < count:
        raise pytest.UsageError("Require 0 <= ci-shard-index < ci-shard-count")


def pytest_collection_modifyitems(config, items):
    index = config.getoption("ci_shard_index")
    count = config.getoption("ci_shard_count")
    selected, deselected = [], []
    for item in items:
        # Keep module fixtures and all parametrizations together. SHA-256 is stable
        # across processes, unlike Python's randomized hash(). New files need no list.
        path = item.path.relative_to(config.rootpath).as_posix()
        shard = int(hashlib.sha256(path.encode()).hexdigest(), 16) % count
        (selected if shard == index else deselected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
