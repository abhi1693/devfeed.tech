"""X measurement stays server-only, normalized, opt-in, and independent of sign-in."""

import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from devfeed_user_api import x_conversions
from devfeed_user_api.config import Settings


def settings(**overrides):
    return Settings(
        _env_file=None,
        DEVFEED_X_PIXEL_ENABLED=True,
        X_PIXEL_TOKEN="test-secret",
        X_SIGNUP_EVENT_ID="tw-pc5f8-testevent",
        **overrides,
    )


def send(config=None, email="  Person@Example.Test  ", twclid=None):
    return x_conversions.send_signup_conversion(
        config or settings(),
        user_id="account-id",
        email=email,
        conversion_time=datetime(2026, 10, 3, 12, 34, 56, 123000, tzinfo=UTC),
        twclid=twclid,
    )


def test_signup_request_normalizes_and_hashes_email(monkeypatch):
    requests = []
    original = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        x_conversions.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert send() is True
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == x_conversions.CONVERSIONS_URL
    assert request.headers["X-Pixel-Token"] == "test-secret"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "conversions": [
            {
                "conversion_time": "2026-10-03T12:34:56.123Z",
                "event_id": "tw-pc5f8-testevent",
                "conversion_id": "signup-account-id",
                "identifiers": [
                    {"hashed_email": hashlib.sha256(b"person@example.test").hexdigest()}
                ],
            }
        ]
    }
    assert b"Person@" not in request.content


@pytest.mark.parametrize("email", [None, "Person@Example.Test"])
def test_click_id_is_sent_with_or_without_email(monkeypatch, email):
    requests = []
    original = httpx.Client
    monkeypatch.setattr(
        x_conversions.httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200)
            ),
            **kwargs,
        ),
    )
    assert send(email=email, twclid="23opevjt88psuo13lu8d020qkn")
    identifiers = json.loads(requests[0].content)["conversions"][0]["identifiers"]
    assert {"twclid": "23opevjt88psuo13lu8d020qkn"} in identifiers
    assert len(identifiers) == (2 if email else 1)


@pytest.mark.parametrize("value", ["", "bad;cookie", "x" * 513, "click\nvalue"])
def test_invalid_click_id_without_email_never_sends(monkeypatch, value):
    monkeypatch.setattr(x_conversions.httpx, "Client", lambda **kwargs: pytest.fail("HTTP called"))
    assert send(email=None, twclid=value) is False


@pytest.mark.parametrize("email", [None, "", "  "])
def test_missing_identifier_never_contacts_x(monkeypatch, email):
    monkeypatch.setattr(x_conversions.httpx, "Client", lambda **kwargs: pytest.fail("HTTP called"))
    assert send(email=email) is False


@pytest.mark.parametrize("missing", ["x_pixel_enabled", "x_pixel_token", "x_signup_event_id"])
def test_missing_configuration_disables_server_events(monkeypatch, missing):
    config = settings()
    setattr(config, missing, False if missing == "x_pixel_enabled" else None)
    monkeypatch.setattr(x_conversions.httpx, "Client", lambda **kwargs: pytest.fail("HTTP called"))
    assert send(config) is False


@pytest.mark.parametrize("status", [200, 302, 400, 401, 429, 500, None])
def test_failures_do_not_escape_or_log_private_data(monkeypatch, caplog, status):
    original = httpx.Client

    def handler(request):
        if status is None:
            raise httpx.ReadTimeout(
                "test-secret Person@Example.Test private-click", request=request
            )
        return httpx.Response(status, text="test-secret Person@Example.Test private-click")

    monkeypatch.setattr(
        x_conversions.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert send(twclid="private-click") is (status == 200)
    assert "test-secret" not in caplog.text
    assert "Person@Example.Test" not in caplog.text
    assert "private-click" not in caplog.text


def test_x_configuration_reads_exact_environment_names(monkeypatch):
    monkeypatch.setenv("X_PIXEL_TOKEN", "test-secret")
    monkeypatch.setenv("X_SIGNUP_EVENT_ID", "tw-pc5f8-testevent")
    config = Settings(_env_file=None)
    assert config.x_pixel_token.get_secret_value() == "test-secret"
    assert config.x_signup_event_id == "tw-pc5f8-testevent"
    assert "test-secret" not in repr(config)
    monkeypatch.setenv("X_PIXEL_TOKEN", "")
    monkeypatch.setenv("X_SIGNUP_EVENT_ID", "")
    config = Settings(_env_file=None)
    assert config.x_pixel_token is None
    assert config.x_signup_event_id is None


@pytest.mark.parametrize("event_id", ["tw-other-abc", "tw-pc5f8-", "tw..."])
def test_invalid_event_id_is_rejected(event_id):
    with pytest.raises(ValueError, match="X sign-up event ID"):
        Settings(_env_file=None, X_SIGNUP_EVENT_ID=event_id)


@pytest.mark.parametrize("value", [None, "", "false", "0", "1", "typo", "true", " TRUE "])
def test_x_pixel_flag_defaults_off_and_requires_true(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DEVFEED_X_PIXEL_ENABLED", raising=False)
    else:
        monkeypatch.setenv("DEVFEED_X_PIXEL_ENABLED", value)
    assert Settings(_env_file=None).x_pixel_enabled is (value in {"true", " TRUE "})
