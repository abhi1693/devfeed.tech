import json
from pathlib import Path

import pytest
from devfeed_core.log_privacy import redact_auth_text, redact_authentication

CASES = json.loads((Path(__file__).parent / "fixtures/auth_redaction_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_authentication_redaction_matches_shared_url_contract(case):
    assert redact_auth_text(case["input"]) == case["expected"]
    assert redact_auth_text(case["expected"]) == case["expected"]


def test_structured_credentials_are_removed_without_losing_status_and_correlation():
    payload = {
        "trace_id": "a" * 32,
        "request_id": "request-123",
        "status": {"code": 2},
        "state": "ready",
        "nested": [{"access_token": "private-token", "password": "private-password"}],
        "attributes": [
            {
                "key": "http.request.header.authorization",
                "value": {"stringValue": "Bearer private-token"},
            },
            {"key": "http.response.status_code", "value": {"intValue": 302}},
        ],
        "message": "Callback /auth/callback?code=private-code&state=private-state",
    }
    result = redact_authentication(payload)
    assert result["trace_id"] == payload["trace_id"]
    assert result["request_id"] == "request-123"
    assert result["status"] == {"code": 2}
    assert result["state"] == "ready"
    assert result["nested"] == [{"access_token": "[REDACTED]", "password": "[REDACTED]"}]
    assert result["message"] == "Callback /auth/callback?code=[REDACTED]&state=[REDACTED]"
    assert result["attributes"] == [
        {"key": "http.request.header.authorization", "value": {"stringValue": "[REDACTED]"}},
        {"key": "http.response.status_code", "value": {"intValue": 302}},
    ]
    assert payload["nested"][0]["access_token"] == "private-token"
