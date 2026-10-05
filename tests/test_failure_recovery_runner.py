"""Report, timeout and resource-ownership safeguards for disposable fault tests."""

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import yaml

from scripts.ci import failure_recovery, failure_services
from scripts.ci.failure_recovery import CASES, check_recovery_report, run_bounded

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "filename,needed",
    [
        ("packages/core/src/recovery-selector-fixture.py", True),
        ("apps/user-api/src/recovery-selector-fixture.py", True),
        ("tests/test_failure_recovery.py", True),
        (".github/workflows/recovery.yml", True),
        ("apps/web/recovery-selector-fixture.ts", False),
        ("docs/recovery-selector-fixture.md", False),
    ],
)
def test_recovery_selection_uses_real_backend_changes_without_mutating_the_checkout(
    tmp_path, filename, needed
):
    git_dir = subprocess.check_output(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=ROOT, text=True
    ).strip()
    objects = tmp_path / "objects"
    objects.mkdir()
    env = {
        **os.environ,
        "GIT_OBJECT_DIRECTORY": str(objects),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(Path(git_dir) / "objects"),
        "GIT_INDEX_FILE": str(tmp_path / "index"),
        "GIT_AUTHOR_NAME": "Recovery test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Recovery test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }

    def git(*args, content=None):
        return subprocess.run(
            ["/usr/bin/git", *args],
            cwd=ROOT,
            env=env,
            input=content,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    base = git("rev-parse", "HEAD")
    git("read-tree", base)
    blob = git("hash-object", "-w", "--stdin", content="recovery selection fixture\n")
    git("update-index", "--add", "--cacheinfo", "100644", blob, filename)
    head = git("commit-tree", git("write-tree"), "-p", base, content="Selector fixture\n")
    workflow = yaml.safe_load((ROOT / ".github/workflows/recovery.yml").read_text())
    selector = next(
        step["run"] for step in workflow["jobs"]["test"]["steps"] if step.get("id") == "select"
    )
    output = tmp_path / "outputs"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", selector],
        cwd=ROOT,
        env={
            **env,
            "CHANGES_ONLY": "true",
            "EVENT_NAME": "pull_request",
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"needed={str(needed).lower()}"


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
    assert check_recovery_report(path) is None


@pytest.mark.parametrize("failure", [None, "pytest", "report", "startup", "cleanup"])
def test_recovery_main_publishes_the_actual_result_and_cleans_up(
    tmp_path, monkeypatch, capsys, failure
):
    monkeypatch.setattr(failure_recovery, "ROOT", tmp_path)
    summary = tmp_path / "job-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    directory = tmp_path / "reports/failure-recovery"
    directory.mkdir(parents=True)
    (directory / "stale-success.json").write_text('{"passed": true}')
    cleaned = []

    @contextmanager
    def services(path):
        assert path == directory
        try:
            if failure == "startup":
                raise RuntimeError("Startup failed")
            yield {"OWNED_TEST": "1"}
            if failure == "cleanup":
                raise RuntimeError("Cleanup failed")
        finally:
            cleaned.append(True)

    def run(command, env, log):
        assert command[:4] == ["uv", "run", "--locked", "--no-build"]
        assert "tests/test_failure_recovery.py" in command
        assert env == {"OWNED_TEST": "1"}
        log.write_text("Application fault assertion failed" if failure == "pytest" else "Passed")
        if failure != "report":
            report(directory / "junit.xml")
        return 1 if failure == "pytest" else 0

    monkeypatch.setattr(failure_recovery, "disposable_failure_services", services)
    monkeypatch.setattr(failure_recovery, "run_bounded", run)
    assert failure_recovery.main() == (1 if failure else 0)
    assert cleaned == [True]
    assert not (directory / "stale-success.json").exists()
    result = json.loads((directory / "result.json").read_text())
    assert result["passed"] is (failure is None)
    assert result["expected_cases"] == 16
    assert result["elapsed_seconds"] >= 0
    assert summary.read_text() == (directory / "summary.md").read_text()
    assert ("**Failed**" if failure else "**Passed**") in summary.read_text()
    if failure == "pytest":
        assert "Application fault assertion failed" in capsys.readouterr().err


def test_bounded_process_returns_its_exit_status_and_log(tmp_path):
    log = tmp_path / "pytest.log"
    status = run_bounded(
        [sys.executable, "-c", "print('recorded failure'); raise SystemExit(7)"],
        os.environ.copy(),
        log,
        timeout=3,
    )
    assert status == 7
    assert log.read_text().strip() == "recorded failure"


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


@pytest.mark.parametrize("kind", ["disconnect", "latency", "stall"])
def test_fault_is_removed_even_when_application_assertions_fail(monkeypatch, kind):
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
            proxy.fault("postgres", kind),
        ):
            raise RuntimeError("Application failure")
        target = "/proxies/postgres" if kind == "disconnect" else "/proxies/postgres/toxics"
        assert [path for _, path, _ in calls] == [target, "/reset"]
        payload = json.loads(calls[0][2])
        if kind == "disconnect":
            assert payload == {"enabled": False}
        else:
            assert payload["type"] == ("latency" if kind == "latency" else "timeout")
            assert payload["attributes"] == (
                {"latency": 700, "jitter": 0} if kind == "latency" else {"timeout": 700}
            )
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
