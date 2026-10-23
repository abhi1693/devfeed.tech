"""Additional reader origins remain explicit and retain browser-session CSRF checks."""

import json
import time
from types import SimpleNamespace

import pytest
from devfeed_user_api import auth, oidc
from devfeed_user_api.config import Settings
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from tests.support.oidc_session_support import provider_session


@pytest.mark.parametrize(
    ("configured", "normalized"),
    [
        ("http://192.0.2.10:3000", "http://192.0.2.10:3000"),
        ("http://localhost:3000/", "http://localhost:3000"),
        ("HTTP://EXAMPLE.COM:80/", "http://example.com"),
        ("HTTPS://EXAMPLE.COM:443", "https://example.com"),
        ("https://example.com:8443/", "https://example.com:8443"),
        ("https://example.com:00443/", "https://example.com"),
        ("http://[::1]:80/", "http://[::1]"),
        ("http://[2001:0DB8::1]:3000/", "http://[2001:db8::1]:3000"),
        ("https://例子.测试/", "https://xn--fsqu00a.xn--0zwm56d"),
        ("http://127.1:80/", "http://127.0.0.1"),
    ],
)
def test_additional_origins_normalize_like_browser_origins(configured, normalized):
    settings = Settings(_env_file=None, allowed_origins=[configured])
    assert settings.allowed_origins == [normalized]
    assert settings.allows_request_origin(normalized)


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "*",
        "https://*",
        "https://*.example.com",
        "https://%2a.example.com",
        "https://user@example.com",
        "https://user:password@example.com",
        "https://@example.com",
        "https://:password@example.com",
        "https://example.com/path",
        "https://example.com/..",
        "https://example.com/%2e",
        "https://example.com//",
        "https://example.com?query=1",
        "https://example.com?",
        "https://example.com#fragment",
        "https://example.com#",
        "https://example.com\\",
        " https://example.com",
        "https://example.com ",
        "https://example.com\n",
        "https://exam\tple.com",
        "https://example.com\x00",
        "https://exa\ufeffmple.com",
        "https://example.com\ufeff",
        "ftp://example.com",
        "file:///example.com",
        "chrome-extension://" + "a" * 32,
        "//example.com",
        "https:example.com",
        "https:///example.com",
        "https://",
        "https://example.com:",
        "https://example.com:abc",
        "https://example.com:-1",
        "https://example.com:65536",
        "https://[::1",
    ],
)
def test_additional_origins_reject_non_origin_configuration(origin):
    with pytest.raises(ValidationError, match="Allowed origins must be exact HTTP or HTTPS"):
        Settings(_env_file=None, allowed_origins=[origin])


def test_allowed_origins_load_from_environment_and_remove_equivalent_duplicates(monkeypatch):
    monkeypatch.setenv(
        "DEVFEED_USER_ALLOWED_ORIGINS",
        '["HTTPS://EXAMPLE.COM:443/", "https://example.com", "http://192.0.2.10:3000"]',
    )
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == ["https://example.com", "http://192.0.2.10:3000"]


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "null",
        "http://192.0.2.11:3000",
        "http://192.0.2.10",
        "https://192.0.2.10:3000",
        "http://192.0.2.10:3000/",
        "http://192.0.2.10:3000.evil.example",
        "http://192.0.2.10:3000@evil.example",
        "chrome-extension://" + "b" * 32,
    ],
)
def test_additional_origin_trust_is_exact(origin):
    settings = Settings(
        _env_file=None,
        base_url="http://localhost:3000",
        cookie_secure=False,
        allowed_origins=["http://192.0.2.10:3000"],
        extension_ids=["a" * 32],
    )
    assert settings.allows_request_origin("http://localhost:3000")
    assert settings.allows_request_origin("http://192.0.2.10:3000")
    assert settings.allows_request_origin("chrome-extension://" + "a" * 32)
    assert not settings.allows_request_origin(origin)


def test_additional_origins_are_opt_in_and_preserve_login_policy_and_callback():
    settings = Settings(_env_file=None, base_url="https://user.example")
    policy = oidc.policy_key(settings)
    assert settings.allowed_origins == []
    assert not settings.allows_request_origin("https://reader.example")
    settings = Settings(
        _env_file=None,
        base_url=settings.base_url,
        allowed_origins=["https://reader.example"],
    )
    assert settings.base_url == "https://user.example"
    assert oidc.redirect_uri(settings) == "https://user.example/api/v1/user/auth/callback"
    assert oidc.policy_key(settings) == policy


@pytest.mark.parametrize("csrf", ["", "x" * 43, "invalid"])
def test_additional_origins_still_require_matching_session_csrf(monkeypatch, csrf):
    settings = Settings(
        _env_file=None,
        base_url="http://localhost:3000",
        cookie_secure=False,
        allowed_origins=["http://192.0.2.10:3000"],
        oidc_issuer_url="https://identity.example",
        oidc_client_id="reader-client",
        oidc_organization_id="reader-organization",
    )
    expires = int(time.time()) + 3600
    session_token = "s" * 43
    session_csrf = "c" * 43
    record = json.dumps(
        {
            "provider_session": provider_session(settings, "reader-subject"),
            "subject": "reader-subject",
            "issuer": settings.oidc_issuer_url,
            "organization_id": settings.oidc_organization_id,
            "user_id": "00000000-0000-4000-8000-000000000001",
            "expires_at": expires,
            "absolute_expires_at": expires,
            "csrf_token": session_csrf,
            "policy": oidc.policy_key(settings),
        }
    ).encode()
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(
        auth,
        "get_redis",
        lambda: SimpleNamespace(
            get=lambda key: record if key == auth.key("session", session_token) else None
        ),
    )
    monkeypatch.setattr(auth, "_record_activity", lambda _: None)

    def request(supplied):
        return Request(
            {
                "type": "http",
                "method": "PUT",
                "headers": [
                    (b"origin", b"http://192.0.2.10:3000"),
                    (b"cookie", f"devfeed_user_session={session_token}".encode()),
                    (b"x-csrf-token", supplied.encode()),
                ],
            }
        )

    assert auth.require_browser_user(request(session_csrf)).user_id
    with pytest.raises(HTTPException) as rejected:
        auth.require_browser_user(request(csrf))
    assert rejected.value.status_code == 403
    settings.allowed_origins = []
    with pytest.raises(HTTPException) as rejected:
        auth.require_browser_user(request(session_csrf))
    assert rejected.value.status_code == 403
