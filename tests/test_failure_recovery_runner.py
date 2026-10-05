"""Report, timeout and resource-ownership safeguards for disposable fault tests."""

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import httpx
import pytest

from scripts.ci import failure_services
from scripts.ci.failure_recovery import CASES, check_recovery_report, run_bounded


def report(path):
    root = ET.Element("testsuite")
    for name, count in CASES.items():
        for index in range(count):
            ET.SubElement(
                root, "testcase", classname="test_failure_recovery", name=f"{name}[{index}]"
            )
    ET.ElementTree(root).write(path)
    return root


def test_recovery_report_requires_every_expected_case(tmp_path):
    path = tmp_path / "junit.xml"
    report(path)
    check_recovery_report(path)


@pytest.mark.parametrize(
    "change", ["empty", "missing", "duplicate", "extra", "failure", "error", "skipped"]
)
def test_recovery_report_rejects_incomplete_or_unsuccessful_cases(tmp_path, change):
    path = tmp_path / "junit.xml"
    root = report(path)
    if change == "empty":
        root.clear()
    elif change == "missing":
        root.remove(root[0])
    elif change == "duplicate":
        root[1].set("name", root[0].get("name"))
    elif change == "extra":
        ET.SubElement(root, "testcase", name="unexpected")
    else:
        ET.SubElement(root[0], change)
    ET.ElementTree(root).write(path)
    with pytest.raises(ValueError):
        check_recovery_report(path)


def test_recovery_timeout_terminates_its_process_group(tmp_path):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="budget"):
        run_bounded(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            os.environ.copy(),
            tmp_path / "log",
            timeout=0.1,
        )
    assert time.monotonic() - started < 3


@pytest.mark.parametrize(
    "url,owner,port",
    [
        ("http://example.com:8474", "owned", "8474"),
        ("http://127.0.0.1:8474", "other", "8474"),
        ("http://127.0.0.1:8474", "owned", "9000"),
    ],
)
def test_fault_control_rejects_unowned_or_mismatched_targets(monkeypatch, url, owner, port):
    def docker(*args):
        if args[0] == "inspect":
            return json.dumps([{"Config": {"Labels": {failure_services.OWNER_LABEL: owner}}}])
        return f"127.0.0.1:{port}"

    monkeypatch.setattr(failure_services, "docker", docker)
    with pytest.raises(ValueError):
        failure_services.FaultProxy(url, "container", "owned")


@pytest.fixture
def owned_proxy(monkeypatch):
    def docker(*args):
        if args[0] == "inspect":
            return json.dumps([{"Config": {"Labels": {failure_services.OWNER_LABEL: "owned"}}}])
        return f"127.0.0.1:{args[-1].split('/')[0]}"

    def respond(request):
        name = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200, json={"upstream": f"{name}:{5432 if name == 'postgres' else 6379}"}
        )

    client = httpx.Client(base_url="http://127.0.0.1:8474", transport=httpx.MockTransport(respond))
    monkeypatch.setattr(failure_services, "docker", docker)
    monkeypatch.setattr(failure_services.httpx, "Client", lambda **kwargs: client)
    proxy = failure_services.FaultProxy("http://127.0.0.1:8474", "container", "owned")
    try:
        yield proxy
    finally:
        proxy.close()


def test_owned_data_targets_are_accepted(owned_proxy):
    owned_proxy.verify_targets(
        "postgresql+psycopg://ci:ci@127.0.0.1:15432/recovery_test",
        "redis://127.0.0.1:16379/15",
    )


@pytest.mark.parametrize(
    "database_url,redis_url",
    [
        ("postgresql+psycopg://ci:ci@example.com:15432/recovery_test", None),
        ("postgresql+psycopg://ci:ci@127.0.0.1:15432/another_test", None),
        ("postgresql+psycopg://ci:ci@127.0.0.1:5432/recovery_test", None),
        ("postgresql://ci:ci@127.0.0.1:15432/recovery_test", None),
        (None, "redis://example.com:16379/15"),
        (None, "redis://127.0.0.1:16379/0"),
        (None, "redis://127.0.0.1:6379/15"),
    ],
)
def test_data_targets_must_match_owned_ports(owned_proxy, database_url, redis_url):
    with pytest.raises(ValueError, match="owned proxy ports"):
        owned_proxy.verify_targets(
            database_url or "postgresql+psycopg://ci:ci@127.0.0.1:15432/recovery_test",
            redis_url or "redis://127.0.0.1:16379/15",
        )


def test_owned_proxy_rejects_external_upstreams(owned_proxy, monkeypatch):
    monkeypatch.setattr(
        owned_proxy,
        "request",
        lambda *args: httpx.Response(200, json={"upstream": "production.example.com:5432"}),
    )
    with pytest.raises(ValueError, match="owned service"):
        owned_proxy.verify_targets(
            "postgresql+psycopg://ci:ci@127.0.0.1:15432/recovery_test",
            "redis://127.0.0.1:16379/15",
        )


def test_fault_is_removed_even_when_application_assertions_fail(monkeypatch):
    calls = []

    def docker(*args):
        if args[0] == "inspect":
            return json.dumps([{"Config": {"Labels": {failure_services.OWNER_LABEL: "owned"}}}])
        return "127.0.0.1:8474"

    def respond(request):
        calls.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json={})

    client = httpx.Client(base_url="http://127.0.0.1:8474", transport=httpx.MockTransport(respond))
    monkeypatch.setattr(failure_services, "docker", docker)
    monkeypatch.setattr(failure_services.httpx, "Client", lambda **kwargs: client)
    proxy = failure_services.FaultProxy("http://127.0.0.1:8474", "container", "owned")
    try:
        with (
            pytest.raises(RuntimeError, match="Application failure"),
            proxy.fault("postgres", "latency"),
        ):
            raise RuntimeError("Application failure")
        assert [path for _, path, _ in calls] == ["/proxies/postgres/toxics", "/reset"]
        with pytest.raises(ValueError), proxy.fault("unknown", "disconnect"):
            pass
        assert len(calls) == 2
    finally:
        proxy.close()


def test_partial_startup_removes_only_resources_created_by_the_suite(tmp_path, monkeypatch):
    cleanup = []

    def docker(*args):
        if args[:2] == ("network", "create"):
            return "owned-network"
        if "postgres" in args:
            return "owned-postgres"
        raise subprocess.CalledProcessError(1, args)

    def command(args, **kwargs):
        cleanup.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(failure_services, "docker", docker)
    monkeypatch.setattr(failure_services.subprocess, "run", command)
    with (
        pytest.raises(subprocess.CalledProcessError),
        failure_services.disposable_failure_services(tmp_path),
    ):
        pass
    assert [args for args in cleanup if args[1] == "rm"] == [
        ["docker", "rm", "-fv", "owned-postgres"]
    ]
    assert [args for args in cleanup if args[1] == "network"] == [
        ["docker", "network", "rm", "owned-network"]
    ]


@pytest.mark.parametrize("failure", ["returncode", "timeout"])
def test_cleanup_continues_after_a_resource_cannot_be_removed(tmp_path, monkeypatch, failure):
    cleanup = []

    def docker(*args):
        if args[:2] == ("network", "create"):
            return "owned-network"
        for role in ("postgres", "redis"):
            if role in args:
                return f"owned-{role}"
        raise subprocess.CalledProcessError(1, args)

    def command(args, **kwargs):
        cleanup.append(args)
        if args[1] == "logs":
            raise subprocess.TimeoutExpired(args, 10)
        if args == ["docker", "rm", "-fv", "owned-redis"]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 30)
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(failure_services, "docker", docker)
    monkeypatch.setattr(failure_services.subprocess, "run", command)
    with (
        pytest.raises(RuntimeError, match="clean up owned recovery resources"),
        failure_services.disposable_failure_services(tmp_path),
    ):
        pass
    assert [args for args in cleanup if args[1] in {"rm", "network"}] == [
        ["docker", "rm", "-fv", "owned-redis"],
        ["docker", "rm", "-fv", "owned-postgres"],
        ["docker", "network", "rm", "owned-network"],
    ]
