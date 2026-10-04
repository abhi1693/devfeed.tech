"""Security regression checks for scan scope, sanitized reports and release enforcement."""

import importlib
import json
from pathlib import Path

import httpx
import pytest

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
