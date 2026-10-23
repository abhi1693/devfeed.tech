"""Security regression checks for scan scope, sanitized reports and release enforcement."""

import errno
import importlib
import json
import os
import socket
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT


@pytest.mark.parametrize("skip_build,fail_build", [(False, False), (True, False), (False, True)])
def test_application_ports_stay_reserved_until_start_and_release_on_cleanup(
    dast, monkeypatch, tmp_path, skip_build, fail_build
):
    from urllib.parse import urlsplit

    observed = set()

    def assert_reserved(origin):
        address = ("127.0.0.1", urlsplit(origin).port)
        observed.add(address)
        with socket.socket() as competitor:
            with pytest.raises(OSError) as error:
                competitor.bind(address)
            assert error.value.errno == errno.EADDRINUSE

    @contextmanager
    def services():
        yield {}

    @contextmanager
    def provider(origin, **kwargs):
        assert_reserved(origin)
        yield "http://127.0.0.1:1", []

    @contextmanager
    def child(command, env, directory, name, **kwargs):
        address = ("127.0.0.1", int(command[command.index("--port") + 1]))
        observed.add(address)
        with socket.socket() as listener:
            # The reservation must hand off immediately before the child starts.
            listener.bind(address)
            yield SimpleNamespace(poll=lambda: None)

    def build(command, **kwargs):
        if command[0] != "npm":
            return
        for key in (
            "DEVFEED_USER_BASE_URL",
            "DEVFEED_ADMIN_BASE_URL",
            "DEVFEED_PUBLIC_API_URL",
            "DEVFEED_USER_API_URL",
            "DEVFEED_ADMIN_API_URL",
        ):
            assert_reserved(kwargs["env"][key])
        if fail_build:
            raise RuntimeError("Build failed")

    monkeypatch.setattr(dast, "ROOT", tmp_path)
    monkeypatch.setattr(dast, "disposable_services", services)
    monkeypatch.setattr(dast, "oidc_provider", provider)
    monkeypatch.setattr(dast, "process", child)
    monkeypatch.setattr(dast, "wait_ready", lambda url, child: assert_reserved(url))
    monkeypatch.setattr(dast, "seed", lambda env: {"slug": "ci-article"})
    monkeypatch.setattr(dast.subprocess, "run", build)
    if fail_build:
        with pytest.raises(RuntimeError, match="Build failed"), ExitStack() as stack:
            dast.stack_apps(stack, tmp_path, skip_build)
    else:
        with ExitStack() as stack:
            _, origins, *_ = dast.stack_apps(stack, tmp_path, skip_build)
            assert len(set(origins.values())) == 5
            for origin in origins.values():
                assert_reserved(origin)
    for address in observed:
        with socket.socket() as available:
            available.bind(address)


@pytest.fixture
def dast(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/ci"))
    module = importlib.import_module("dast")
    monkeypatch.setattr(module, "REPORTS", tmp_path / "reports")
    return module


@pytest.mark.parametrize(
    "origin",
    [
        "https://devfeed.tech",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000/path",
        "http://ci:secret@127.0.0.1:8000",
        "http://127.0.0.1:8000?token=secret",
        "http://127.0.0.1:8000#fragment",
    ],
)
def test_dast_never_accepts_external_or_credentialed_targets(dast, origin):
    with pytest.raises(ValueError):
        dast.local_origin(origin)


def test_dast_reports_remove_credentials_payloads_and_callback_values(dast):
    origin = "http://127.0.0.1:8000"
    alerts = [
        {
            "riskcode": "3",
            "pluginId": "40012",
            "name": "Cross Site Scripting",
            "url": origin + "/api/v1/user/auth/callback?code=secret&state=secret",
            "evidence": "secret",
            "attack": "secret",
            "param": "secret",
            "requestHeader": "Cookie: secret",
            "responseBody": "secret",
        }
    ]
    report = dast.findings(alerts, {origin})
    assert report[0]["url"] == origin + "/api/v1/user/auth/callback"
    assert "secret" not in json.dumps(report)
    assert dast.findings([{**alerts[0], "risk": "High", "riskcode": "3"}], {origin}) == report
    api_alert = {key: value for key, value in alerts[0].items() if key != "riskcode"}
    assert dast.findings([{**api_alert, "risk": "High"}], {origin}) == report
    with pytest.raises(ValueError):
        dast.findings([{**alerts[0], "url": "https://devfeed.tech/"}], {origin})


@pytest.mark.parametrize("risk,passed", [(0, True), (1, True), (2, True), (3, False)])
def test_dast_blocks_high_risk_and_preserves_lower_risk_findings(dast, risk, passed):
    report = {
        "completed": True,
        "mode": "passive",
        "traffic": {"web": 10},
        "authenticated": ["reader", "admin"],
        "active": [],
        "findings": [
            {"rule": "40012", "risk": risk, "name": "Example", "url": "http://127.0.0.1:8000/"}
        ],
    }
    assert dast.write_report(report) is passed
    assert json.loads((dast.REPORTS / "results.json").read_text()) == report
    assert "rule 40012" in (dast.REPORTS / "summary.md").read_text()


def test_dast_failed_setup_overwrites_an_earlier_success(dast):
    dast.REPORTS.mkdir()
    (dast.REPORTS / "results.json").write_text('{"completed": true}')
    assert dast.write_report({"completed": False, "mode": "active"}) is False
    assert json.loads((dast.REPORTS / "results.json").read_text())["completed"] is False


def test_dast_rejects_zap_api_errors(dast):
    zap = dast.Zap("http://127.0.0.1:8000", "secret")
    zap.client.close()
    zap.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": "bad_action", "message": "secret"})
        )
    )
    try:
        with pytest.raises(RuntimeError, match="ZAP API failed") as exc:
            zap.call("ascan", "action", "scan")
        assert "secret" not in str(exc.value)
    finally:
        zap.client.close()


@pytest.fixture
def zap_startup(dast, monkeypatch):
    events = []
    key = "known-private-zap-api-key"
    state = {
        "Running": False,
        "ExitCode": 137,
        "OOMKilled": True,
        "Error": f"startup failure {key}",
        "Config": {"Env": ["unrelated-private-value"]},
    }
    logs = "prefix" * 4000 + key + " startup-tail\n"
    monkeypatch.setattr(dast, "port", lambda: 48123)
    monkeypatch.setattr(dast.secrets, "token_urlsafe", lambda size: key)
    monkeypatch.setattr(dast.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(
        dast, "docker", lambda *args: events.append(("start", args)) or "owned-zap-container"
    )

    def request(*args, **kwargs):
        events.append(("version",))
        raise httpx.ConnectError("not ready")

    def run(command, **kwargs):
        events.append((command[1], command, kwargs))
        if command[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps(state), stderr="")
        if command[1] == "logs":
            return SimpleNamespace(stdout=logs, stderr="stderr-tail\n")
        assert command == ["docker", "rm", "-f", "owned-zap-container"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(dast.Zap, "call", request)
    monkeypatch.setattr(dast.subprocess, "run", run)
    return SimpleNamespace(events=events, key=key, state=state, logs=logs, run=run)


def test_zap_startup_timeout_retains_bounded_redacted_evidence_before_cleanup(dast, zap_startup):
    with (
        pytest.raises(RuntimeError, match="^Pinned ZAP did not become ready$"),
        dast.zap_server(),
    ):
        pytest.fail("An unready scanner must not scan")
    events = zap_startup.events
    assert sum(event[0] == "version" for event in events) == 120
    assert [event for event in events if event[0] == "sleep"] == [("sleep", 1)] * 120
    assert [event[0] for event in events[-3:]] == ["inspect", "logs", "rm"]
    start = events[0][1]
    assert "--rm" not in start
    assert start[start.index("--memory") + 1] == "2g"
    assert start[start.index("--cpus") + 1] == "2"
    assert "start.checkForUpdates=false" in start
    assert "start.downloadNewRelease=false" in start
    report_text = (dast.REPORTS / "zap-startup.json").read_text()
    report = json.loads(report_text)
    assert report["state"] == {
        "Running": False,
        "ExitCode": 137,
        "OOMKilled": True,
        "Error": "startup failure [REDACTED]",
    }
    expected_tail = (zap_startup.logs + "stderr-tail\n").replace(zap_startup.key, "[REDACTED]")[
        -16384:
    ]
    assert report["log_tail"] == expected_tail
    assert "[REDACTED] startup-tail" in report["log_tail"]
    assert len(report["log_tail"]) <= 16384
    assert report["diagnostic_errors"] == []
    assert zap_startup.key not in report_text
    assert "unrelated-private-value" not in report_text
    inspect = next(event for event in events if event[0] == "inspect")
    assert ".Config" not in inspect[1][3] and ".State" in inspect[1][3]
    assert inspect[2]["timeout"] == 5


def test_zap_startup_success_keeps_strict_version_without_failure_artifacts(
    dast, zap_startup, monkeypatch
):
    monkeypatch.setattr(dast.Zap, "call", lambda *args: {"version": "2.17.0"})
    with dast.zap_server() as zap:
        assert zap.key == zap_startup.key
    assert [event[0] for event in zap_startup.events] == ["start", "rm"]
    assert not (dast.REPORTS / "zap-startup.json").exists()


def test_zap_startup_does_not_accept_a_different_ready_version(dast, zap_startup, monkeypatch):
    monkeypatch.setattr(dast.Zap, "call", lambda *args: {"version": "2.16.0"})
    with (
        pytest.raises(RuntimeError, match="^Pinned ZAP did not become ready$"),
        dast.zap_server(),
    ):
        pytest.fail("A different scanner version must not scan")
    assert sum(event[0] == "sleep" for event in zap_startup.events) == 120
    assert zap_startup.events[-1][0] == "rm"


@pytest.mark.parametrize("failure", ["inspect", "logs", "write"])
def test_zap_startup_diagnostic_failures_preserve_readiness_failure_and_cleanup(
    dast, zap_startup, monkeypatch, failure, capsys
):
    def run(command, **kwargs):
        if command[1] == failure:
            raise subprocess.TimeoutExpired(command, 5, output=zap_startup.key)
        return zap_startup.run(command, **kwargs)

    monkeypatch.setattr(dast.subprocess, "run", run)
    if failure == "write":

        def fail_write(*args, **kwargs):
            raise OSError(zap_startup.key)

        monkeypatch.setattr(Path, "write_text", fail_write)
    with (
        pytest.raises(RuntimeError, match="^Pinned ZAP did not become ready$"),
        dast.zap_server(),
    ):
        pytest.fail("Failed diagnostics must not allow scanning")
    assert zap_startup.events[-1][0] == "rm"
    assert sum(event[0] == "version" for event in zap_startup.events) == 120
    captured = capsys.readouterr()
    assert zap_startup.key not in captured.out + captured.err
    if failure != "write":
        report_text = (dast.REPORTS / "zap-startup.json").read_text()
        report = json.loads(report_text)
        assert len(report["diagnostic_errors"]) == 1
        assert zap_startup.key not in report_text


@pytest.mark.parametrize("field,value", [("Running", "false"), ("ExitCode", True)])
def test_zap_startup_rejects_malformed_state_without_serializing_it(
    dast, zap_startup, field, value
):
    zap_startup.state[field] = value
    dast.zap_startup_diagnostics("owned-zap-container", zap_startup.key)
    report = json.loads((dast.REPORTS / "zap-startup.json").read_text())
    assert report["state"] == {}
    assert report["diagnostic_errors"] == ["Container state unavailable"]
    assert report["log_tail"].endswith("startup-tail\nstderr-tail\n")


@pytest.mark.parametrize("operation", ["close", "rm"])
@pytest.mark.parametrize("phase", ["startup", "scan", "success"])
def test_zap_cleanup_attempts_removal_and_preserves_an_existing_failure(
    dast, zap_startup, monkeypatch, capsys, operation, phase
):
    close = dast.httpx.Client.close

    def close_client(client):
        zap_startup.events.append(("close",))
        close(client)
        if operation == "close":
            raise RuntimeError(zap_startup.key)

    def run(command, **kwargs):
        if command[1] == "rm" and operation == "rm":
            zap_startup.events.append(("rm", command, kwargs))
            raise subprocess.CalledProcessError(1, command, stderr=zap_startup.key)
        return zap_startup.run(command, **kwargs)

    monkeypatch.setattr(dast.httpx.Client, "close", close_client)
    monkeypatch.setattr(dast.subprocess, "run", run)
    if phase != "startup":
        monkeypatch.setattr(dast.Zap, "call", lambda *args: {"version": "2.17.0"})
    message = {
        "startup": "Pinned ZAP did not become ready",
        "scan": "Original scan failure",
        "success": "Unable to fully clean up CI-owned ZAP resources",
    }[phase]
    with pytest.raises(RuntimeError, match=f"^{message}$"), dast.zap_server():
        if phase == "scan":
            raise RuntimeError(message)
        assert phase == "success"
    assert [event[0] for event in zap_startup.events[-2:]] == ["close", "rm"]
    captured = capsys.readouterr()
    assert zap_startup.key not in captured.out + captured.err
    if phase != "success":
        assert "Unable to fully clean up CI-owned ZAP resources" in captured.err


def test_dast_active_scan_requires_real_attack_requests(dast, monkeypatch):
    zap = dast.Zap("http://127.0.0.1:8000", "secret")

    def response(component, kind, operation, **params):
        if operation == "scan":
            return {"scan": "0"}
        if operation == "status":
            return {"status": "100"}
        if operation == "messagesIds":
            return {"messagesIds": []}
        return {"Result": "OK"}

    monkeypatch.setattr(zap, "call", response)
    try:
        with pytest.raises(RuntimeError, match="without attack requests"):
            zap.active(["http://127.0.0.1:8000/search?q=CI"], "1")
    finally:
        zap.client.close()


def test_master_image_validation_depends_on_dast():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    validation = workflow.split("  validation:\n", 1)[1].split("  containers:\n", 1)[0]
    assert "dast" in validation.split("    needs:", 1)[1].split("\n", 1)[0]
    containers = workflow.split("  containers:\n", 1)[1].split("  mutation:\n", 1)[0]
    assert "    needs: validation\n" in containers


def plugin_progress(status="Complete", requests="10"):
    return [
        "http://127.0.0.1:8000",
        {
            "HostProcess": [
                {"Plugin": ["XSS", "40012", "release", status, "100", requests, "0"]},
                {"Plugin": ["SQL", "40018", "release", "Complete", "100", "10", "0"]},
            ]
        },
    ]


@pytest.mark.parametrize("status", ["Skipped", "Pending", "50%"])
def test_dast_rejects_rules_stopped_or_skipped_by_zap(dast, status):
    # ZAP's overall status can be 100 even when a scan was forcibly stopped.
    with pytest.raises(RuntimeError, match="skipped, stopped"):
        dast.completed_rules(plugin_progress(status))


def test_dast_requires_both_rules_and_validates_progress_shape(dast):
    assert dast.completed_rules(plugin_progress()) == [
        {"id": "40012", "requests": 10},
        {"id": "40018", "requests": 10},
    ]
    with pytest.raises(RuntimeError, match="did not complete"):
        dast.completed_rules([])
    with pytest.raises(RuntimeError, match="unsupported format"):
        dast.completed_rules([{"HostProcess": [{"Plugin": ["malformed"]}]}])
    with pytest.raises(RuntimeError, match="did not send attacks"):
        dast.completed_rules(plugin_progress(requests="0"))


def authenticated_sessions():
    return {
        "reader": {"cookie": "devfeed_user_session=reader-secret", "csrf": "reader-csrf"},
        "admin": {"cookie": "devfeed_admin_session=admin-secret", "csrf": "admin-csrf"},
    }


def test_browser_scan_uses_private_pipes_and_keeps_credentials_out_of_logs(
    dast, monkeypatch, capsys
):
    sessions = authenticated_sessions()
    settings = {"web": "http://127.0.0.1:8000", "origins": ["http://127.0.0.1:8000"]}

    def run(command, **options):
        assert command[-2:] == ["node", "scripts/testing/dast-browser.mjs"]
        assert "--reporter=lcov" in command and "--reporter=json-summary" in command
        assert json.loads(options["input"]) == settings
        assert options["capture_output"] and options["text"]
        assert options["timeout"] == 240 and options["cwd"] == dast.ROOT
        assert options["env"] == {"OWNED": "fixture"}
        return SimpleNamespace(returncode=0, stdout=json.dumps(sessions), stderr="secret")

    monkeypatch.setattr(dast.subprocess, "run", run)
    assert dast.browser_sessions(settings, {"OWNED": "fixture"}) == sessions
    captured = capsys.readouterr()
    assert "secret" not in captured.out + captured.err


@pytest.mark.parametrize(
    "payload",
    [
        "secret invalid JSON",
        "null",
        "[]",
        "{}",
        json.dumps({"reader": authenticated_sessions()["reader"]}),
        json.dumps({**authenticated_sessions(), "admin": "secret"}),
        json.dumps({**authenticated_sessions(), "admin": {"cookie": "secret"}}),
        json.dumps(
            {
                **authenticated_sessions(),
                "admin": {"cookie": "devfeed_user_session=secret", "csrf": "secret"},
            }
        ),
        json.dumps(
            {
                **authenticated_sessions(),
                "reader": {"cookie": "devfeed_user_session=", "csrf": "secret"},
            }
        ),
        json.dumps(
            {
                **authenticated_sessions(),
                "reader": {"cookie": "devfeed_user_session=secret\r\n", "csrf": "secret"},
            }
        ),
        json.dumps({**authenticated_sessions(), "reader": {"cookie": None, "csrf": "secret"}}),
        json.dumps(
            {
                **authenticated_sessions(),
                "reader": {"cookie": "devfeed_user_session=secret", "csrf": ""},
            }
        ),
        json.dumps(
            {
                **authenticated_sessions(),
                "reader": {"cookie": "devfeed_user_session=secret", "csrf": 1},
            }
        ),
    ],
)
def test_browser_scan_rejects_missing_or_malformed_sessions_without_disclosing_them(
    dast, monkeypatch, payload
):
    monkeypatch.setattr(
        dast.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload),
    )
    with pytest.raises(RuntimeError, match="complete authenticated sessions") as error:
        dast.browser_sessions({}, {})
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("checkpoint", ["content", "login", "launch", "cookie-secret"])
@pytest.mark.parametrize("page", ["12", "16", "cookie-secret"])
def test_browser_scan_failure_reports_only_known_checkpoints_without_credentials(
    dast, monkeypatch, checkpoint, page
):
    monkeypatch.setattr(
        dast.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="secret",
            stderr=(
                f"secret callback?code=secret\nDAST_BROWSER_FAILURE:{checkpoint}\n"
                f"DAST_BROWSER_PAGE:{page}\n"
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="browser scan failed") as error:
        dast.browser_sessions({}, {})
    assert "secret" not in str(error.value)
    expected = "" if checkpoint == "cookie-secret" else f" at {checkpoint}"
    if page == "12":
        expected += " (page 12)"
    assert str(error.value) == f"Authenticated browser scan failed{expected}"


def test_browser_failure_identifies_javascript_and_selector_errors_without_logging_messages(
    dast, monkeypatch
):
    monkeypatch.setattr(
        dast.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="secret",
            stderr=(
                "secret callback?code=secret\nDAST_BROWSER_JS\nDAST_BROWSER_STRICT\n"
                "DAST_BROWSER_FAILURE:content\n"
            ),
        ),
    )
    with pytest.raises(
        RuntimeError, match="at content .*JavaScript exception.*ambiguous selector"
    ) as error:
        dast.browser_sessions({}, {})
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("needed", [False, True])
def test_zap_selection_uses_real_commits_and_system_git_with_an_empty_path(tmp_path, needed):
    workflow = yaml.safe_load((ROOT / ".github/workflows/dast.yml").read_text())
    script = next(
        step["run"] for step in workflow["jobs"]["scan"]["steps"] if step.get("id") == "select"
    )
    # Isolated commit objects use this checkout's tree, without depending on fetched history
    # or changing the repository's refs, index or working tree.
    objects = tmp_path / "objects"
    objects.mkdir()
    git_env = {
        **os.environ,
        "GIT_OBJECT_DIRECTORY": str(objects),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "--path-format=absolute", "--git-path", "objects"],
            cwd=ROOT,
            text=True,
        ).strip(),
        "GIT_AUTHOR_NAME": "CI fixture",
        "GIT_AUTHOR_EMAIL": "ci@example.invalid",
        "GIT_COMMITTER_NAME": "CI fixture",
        "GIT_COMMITTER_EMAIL": "ci@example.invalid",
    }

    def git(*args, input=None):
        return subprocess.check_output(
            ["/usr/bin/git", *args], cwd=ROOT, env=git_env, text=True, input=input
        ).strip()

    base = git("commit-tree", git("mktree", input=""), "-m", "Empty fixture")
    head = git("commit-tree", git("rev-parse", "HEAD^{tree}"), "-p", base, "-m", "Runtime fixture")
    if not needed:
        base = head
    output, summary = tmp_path / "output", tmp_path / "summary"
    subprocess.run(
        ["/usr/bin/bash", "-e", "-o", "pipefail", "-c", script],
        cwd=ROOT,
        check=True,
        env={
            **git_env,
            "PATH": "",
            "CHANGES_ONLY": "true",
            "EVENT_NAME": "pull_request",
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
    )
    assert output.read_text() == f"needed={str(needed).lower()}\n"
    if not needed:
        assert "no runtime changes" in summary.read_text()


def test_zap_selection_rejects_argument_injection_before_running_git(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/dast.yml").read_text())
    script = next(
        step["run"] for step in workflow["jobs"]["scan"]["steps"] if step.get("id") == "select"
    )
    result = subprocess.run(
        ["/usr/bin/bash", "-e", "-o", "pipefail", "-c", script],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": "",
            "CHANGES_ONLY": "true",
            "EVENT_NAME": "pull_request",
            "BASE_SHA": "--output=/tmp/secret",
            "HEAD_SHA": "a" * 40,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        },
        capture_output=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "zap-files.txt").exists()
