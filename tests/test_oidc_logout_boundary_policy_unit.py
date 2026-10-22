"""Signed provider logout and browser completion preserve correlation and safe failures.

Redis methods return prepared boundary results. Lua execution, compare-and-delete
atomicity and cross-process races remain covered by the disposable-Redis tests.
"""

import asyncio
import json
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from devfeed_http import oidc
from devfeed_http import oidc_logout as logout
from devfeed_user_api import auth
from devfeed_user_api.config import Settings
from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request
from test_oidc_logout import claims, sign
from test_oidc_logout import signed as signed

ORIGIN = "https://user.example"


def settings():
    return Settings(
        _env_file=None,
        base_url=ORIGIN,
        allowed_origins=["https://reader.example"],
        oidc_issuer_url="https://issuer.test",
        oidc_client_id="reader",
        oidc_organization_id="org",
    )


def session_record(configuration, *, sid="provider-session"):
    return {
        "subject": "reader-subject",
        "provider_session": {
            "issuer": configuration.oidc_issuer_url,
            "client_id": configuration.oidc_client_id,
            "subject": "reader-subject",
            "sid": sid,
            "issued_at": int(time.time()),
        },
    }


@pytest.mark.parametrize(
    "change", [{"sub": ""}, {"sub": "reader\nsubject"}, {"sid": None}, {"sid": " "}]
)
def test_correlation_refuses_invalid_subject_or_provider_session_identity(change):
    configuration = settings()
    with pytest.raises(oidc.OIDCError, match="Invalid provider session claims"):
        logout.correlated(
            {
                "iss": configuration.oidc_issuer_url,
                "sub": "reader-subject",
                "iat": int(time.time()),
                **change,
            },
            configuration.oidc_client_id,
        )


@pytest.mark.parametrize(
    "change",
    ["legacy", "issuer", "client", "subject", "boolean_time", "invalid_subject", "invalid_sid"],
)
def test_browser_record_without_current_typed_provider_correlation_is_not_authenticated(change):
    configuration = settings()
    record = session_record(configuration)
    if change == "legacy":
        record.pop("provider_session")
    else:
        name, value = {
            "issuer": ("issuer", "https://other.example"),
            "client": ("client_id", "administrator"),
            "subject": ("subject", "another-reader"),
            "boolean_time": ("issued_at", True),
            "invalid_subject": ("subject", " "),
            "invalid_sid": ("sid", "provider\nidentity"),
        }[change]
        record["provider_session"][name] = value
    redis = Mock()
    assert not logout.active(redis, "user", configuration, record)
    redis.get.assert_not_called()


@pytest.mark.parametrize(
    "revoked", ["none", "subject-cutoff", "old-cutoff", "sid", "sid-and-subject", "sidless"]
)
def test_revocation_reads_are_bounded_and_keep_subject_cutoff_separate_from_session_markers(
    revoked,
):
    configuration = settings()
    record = session_record(configuration, sid=None if revoked == "sidless" else "provider-session")
    context = record["provider_session"]
    prefix = logout.scope("user", context["issuer"], context["client_id"])
    values = {}
    if revoked in {"subject-cutoff", "old-cutoff"}:
        values[logout.marker(prefix, None, context["subject"])] = str(
            context["issued_at"] - int(revoked == "old-cutoff")
        )
    elif revoked in {"sid", "sid-and-subject"}:
        values[
            logout.marker(
                prefix, context["sid"], context["subject"] if revoked == "sid-and-subject" else None
            )
        ] = "1"
    redis = Mock()
    redis.get.side_effect = values.get
    assert logout.active(redis, "user", configuration, record) is (
        revoked in {"none", "old-cutoff", "sidless"}
    )
    assert 1 <= redis.get.call_count <= 3
    assert redis.get.call_count == (
        1 if revoked in {"subject-cutoff", "sidless"} else 2 if revoked == "sid" else 3
    )
    assert all(
        "reader-subject" not in call.args[0] and "provider-session" not in call.args[0]
        for call in redis.get.call_args_list
    )
    assert prefix != logout.scope("admin", context["issuer"], context["client_id"])


@pytest.mark.parametrize("revoked", [False, True])
def test_session_save_removes_a_write_if_provider_revocation_is_already_visible(revoked):
    configuration, redis = settings(), Mock()
    record = session_record(configuration)
    redis.get.return_value = str(record["provider_session"]["issued_at"]) if revoked else None
    if revoked:
        with pytest.raises(oidc.OIDCError, match="Provider session has ended"):
            logout.save(redis, "user", configuration, "opaque-session-key", record, 3600)
        redis.delete.assert_called_once_with("opaque-session-key")
    else:
        logout.save(redis, "user", configuration, "opaque-session-key", record, 3600)
        redis.delete.assert_not_called()
    assert json.loads(redis.set.call_args.args[1]) == record
    assert redis.set.call_args.kwargs == {"ex": 3600}


@pytest.mark.parametrize(
    "case", ["valid", "query", "json", "duplicate", "empty", "non-ascii", "too-many", "oversized"]
)
def test_logout_form_parser_rejects_ambiguous_or_unbounded_transport_before_signature_work(case):
    body = {
        "duplicate": b"logout_token=one&logout_token=two",
        "empty": b"logout_token=",
        "non-ascii": b"logout_token=\xff",
        "too-many": b"logout_token=one&" + b"field=x&" * 33,
        "oversized": b"x" * (logout.MAX_BODY + 1),
    }.get(case, b"logout_token=provider-token")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/logout",
            "query_string": b"state=ambiguous" if case == "query" else b"",
            "headers": [
                (
                    b"content-type",
                    b"application/json"
                    if case == "json"
                    else b"application/x-www-form-urlencoded; charset=utf-8",
                )
            ],
        },
        receive,
    )
    if case == "valid":
        assert asyncio.run(logout.read_token(request)) == "provider-token"
    else:
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(logout.read_token(request))
        assert rejected.value.status_code == (413 if case == "oversized" else 400)


@pytest.fixture
def browser(monkeypatch, signed):
    configuration, redis = settings(), Mock()
    redis.eval.return_value = 1
    redis.get.return_value = redis.getdel.return_value = None
    metadata = {
        **signed.metadata,
        "issuer": configuration.oidc_issuer_url,
        "end_session_endpoint": "https://issuer.test/logout?mode=browser",
    }
    discover = Mock(return_value=metadata)
    monkeypatch.setattr(oidc, "discovery", discover)
    monkeypatch.setattr(auth, "get_settings", lambda: configuration)

    def local_logout(request):
        response = Response()
        auth.cookie(response, "session", "", 0)
        return response

    local = Mock(side_effect=local_logout)
    router = APIRouter()
    logout.add_routes(
        router,
        service="user",
        get_settings=lambda: configuration,
        get_redis=lambda: redis,
        local_logout=local,
        cookie=auth.cookie,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, base_url=ORIGIN) as client:
        yield NS(
            client=client,
            redis=redis,
            settings=configuration,
            metadata=metadata,
            discover=discover,
            local=local,
            signed=signed,
        )


@pytest.mark.parametrize(
    "failure", [None, "unconfigured", "discovery", "keys", "redis", "signature", "conflict"]
)
def test_backchannel_signed_delivery_classifies_dependency_failures_and_invalid_tokens(
    browser, monkeypatch, failure
):
    value = browser
    token = sign(value.signed, claims())
    if failure == "unconfigured":
        value.settings.oidc_issuer_url = None
    elif failure == "discovery":
        value.discover.side_effect = oidc.OIDCError("private discovery details")
    elif failure == "keys":
        monkeypatch.setattr(
            oidc, "request_json", Mock(side_effect=oidc.OIDCDependencyError("private key endpoint"))
        )
    elif failure == "redis":
        value.redis.eval.side_effect = RedisConnectionError("redis://secret@internal")
    elif failure == "signature":
        parts = token.split(".")
        parts[-1] = ("A" if parts[-1][0] != "A" else "B") + parts[-1][1:]
        token = ".".join(parts)
    elif failure == "conflict":
        value.redis.eval.return_value = -1
    response = value.client.post(
        "/backchannel-logout",
        content=urlencode({"logout_token": token}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == (
        200 if failure is None else 400 if failure in {"signature", "conflict"} else 503
    )
    assert "private" not in response.text and "secret" not in response.text
    if failure is None:
        assert response.content == b"" and response.headers["cache-control"] == "no-store"
        arguments = value.redis.eval.call_args.args
        assert arguments[0] == logout.RECORD_LOGOUT and arguments[1] == 2
        assert arguments[-2:] == (logout.RETENTION, logout.MAX_AGE + 2 * logout.SKEW)
        assert "provider-session" not in arguments[2] and "provider-session" not in arguments[3]
    value.local.assert_not_called()


@pytest.mark.parametrize("failure", ["unconfigured", "discovery", "cross-origin", "redis"])
def test_sso_start_requires_trusted_configured_provider_and_keeps_operational_errors_private(
    browser, failure
):
    value = browser
    if failure == "unconfigured":
        value.settings.oidc_issuer_url = None
    elif failure == "discovery":
        value.discover.side_effect = oidc.OIDCError("private provider response")
    elif failure == "cross-origin":
        value.metadata["end_session_endpoint"] = "https://attacker.example/logout"
    else:
        value.redis.set.side_effect = RedisConnectionError("private redis password")
    response = value.client.post("/sso/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 503 and "private" not in response.text
    value.local.assert_not_called()
    assert not response.headers.get_list("set-cookie")


@pytest.mark.parametrize(
    "completion", ["success", "alias", "browser", "policy", "origin", "replay", "redis", "query"]
)
def test_sso_completion_is_browser_bound_single_use_and_can_only_return_to_trusted_origin(
    browser, completion
):
    value = browser
    origin = "https://reader.example" if completion == "alias" else ORIGIN
    response = value.client.post("/sso/logout", headers={"Origin": origin})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    url = urlsplit(response.json()["url"])
    parameters = parse_qs(url.query)
    assert url.netloc == "issuer.test" and parameters["mode"] == ["browser"]
    assert parameters["post_logout_redirect_uri"] == [origin + "/api/v1/user/auth/sso/callback"]
    assert "id_token_hint" not in parameters
    assert any(
        "devfeed_user_session=" in header and "Max-Age=0" in header
        for header in response.headers.get_list("set-cookie")
    )
    value.local.assert_called_once()
    key, raw = value.redis.set.call_args.args
    assert value.redis.set.call_args.kwargs == {"ex": oidc.FLOW_TTL}
    flow = json.loads(raw)
    if completion == "browser":
        flow["browser"] = "x" * 43
    elif completion == "policy":
        flow["policy"][1] = "different-client"
    elif completion == "origin":
        flow["completion_origin"] = "https://attacker.example"
    if completion == "redis":
        value.redis.get.side_effect = RedisConnectionError("private details")
    else:
        value.redis.get.return_value = json.dumps(flow)
        value.redis.getdel.return_value = None if completion == "replay" else json.dumps(flow)
    query = [("state", parameters["state"][0])]
    if completion == "query":
        query.append(("state", parameters["state"][0]))
    finished = value.client.get("/sso/callback", params=query, follow_redirects=False)
    assert finished.status_code == (
        302 if completion in {"success", "alias"} else 503 if completion == "redis" else 400
    )
    assert "private" not in finished.text
    if completion in {"success", "alias"}:
        assert finished.headers["location"] == origin + "/login?logout=sso"
        assert finished.headers["cache-control"] == "no-store"
        value.redis.getdel.assert_called_once_with(key)
    elif completion not in {"replay", "redis"}:
        value.redis.getdel.assert_not_called()
