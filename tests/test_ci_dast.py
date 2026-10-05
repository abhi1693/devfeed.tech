"""Security regression checks for scan scope, sanitized reports and release enforcement."""

import importlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


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


def test_dast_release_images_depend_on_scan():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    release = workflow.split("  release-images:\n", 1)[1].split("  required:\n", 1)[0]
    assert "      - dast\n" in release


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
def test_browser_scan_failure_reports_only_known_checkpoints_without_credentials(
    dast, monkeypatch, checkpoint
):
    monkeypatch.setattr(
        dast.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="secret",
            stderr=f"secret callback?code=secret\nDAST_BROWSER_FAILURE:{checkpoint}\n",
        ),
    )
    with pytest.raises(RuntimeError, match="browser scan failed") as error:
        dast.browser_sessions({}, {})
    assert "secret" not in str(error.value)
    expected = "" if checkpoint == "cookie-secret" else f" at {checkpoint}"
    assert str(error.value) == f"Authenticated browser scan failed{expected}"


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
