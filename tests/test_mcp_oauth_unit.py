"""OAuth policy boundaries; Redis atomicity remains covered by integration tests."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_mcp import oauth
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull

RESOURCE = "https://mcp.example/mcp"
NOW = 1_000_000


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def provider(monkeypatch):
    records = {}

    async def put(identifier, value, **_):
        records[identifier] = value

    async def remove(*identifiers):
        for identifier in identifiers:
            records.pop(identifier, None)

    store = SimpleNamespace(
        records=records,
        get=AsyncMock(side_effect=records.get),
        set=AsyncMock(side_effect=put),
        getdel=AsyncMock(side_effect=lambda identifier: records.pop(identifier, None)),
        delete=AsyncMock(side_effect=remove),
        incr=AsyncMock(return_value=1),
        expire=AsyncMock(),
        eval=AsyncMock(),
        zrem=AsyncMock(),
    )
    monkeypatch.setattr(oauth.time, "time", lambda: NOW)
    return oauth.OAuthProvider(store, RESOURCE, "https://reader.example/", 900)


def client(**changes):
    return OAuthClientInformationFull(
        **{
            "client_id": "client",
            "client_name": "Reader agent",
            "token_endpoint_auth_method": "none",
            "redirect_uris": ["https://agent.example/callback"],
            **changes,
        }
    )


def record(provider, kind, token, value):
    provider.redis.records[oauth.key(kind, token)] = json.dumps(value)


def grant(**changes):
    return {
        "client_id": "client",
        "expires_at": NOW + 3600,
        "absolute_expires_at": NOW + 90 * 86400,
        "resource": RESOURCE,
        "identity": {"user_id": "reader"},
        "scopes": oauth.SCOPES,
        **changes,
    }


def token_record(**changes):
    return {
        "client_id": "client",
        "scopes": ["devfeed:read"],
        "grant_id": "grant",
        "expires_at": NOW + 900,
        "resource": RESOURCE,
        "subject": "reader",
        **changes,
    }


@pytest.mark.parametrize(
    "redirect",
    ["https://agent.example/cb", "http://localhost/cb", "http://127.0.0.1/cb", "http://[::1]/cb"],
)
def test_public_registration_round_trips_and_hashes_storage_keys(provider, redirect):
    account = client(redirect_uris=[redirect])
    assert run(provider.get_client(account.client_id)) is None
    run(provider.register_client(account))
    assert run(provider.get_client(account.client_id)) == account
    assert provider.redis.set.await_args.kwargs["ex"] == 90 * 86400
    assert "client" not in oauth.key("access", "client").split(":")[-1]
    assert provider.redis.expire.await_args.args[1] == 120


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"token_endpoint_auth_method": "client_secret_post"}, "invalid_client_metadata"),
        ({"redirect_uris": []}, "invalid_redirect_uri"),
        ({"redirect_uris": ["https://agent.example/cb"] * 11}, "invalid_redirect_uri"),
        ({"redirect_uris": ["http://agent.example/cb"]}, "invalid_redirect_uri"),
        ({"redirect_uris": ["https://name:password@agent.example/cb"]}, "invalid_redirect_uri"),
        ({"redirect_uris": ["https://agent.example/cb#fragment"]}, "invalid_redirect_uri"),
        ({"redirect_uris": ["https://agent.example/" + "x" * 2000]}, "invalid_redirect_uri"),
        ({"redirect_uris": ["custom:/callback"]}, "invalid_redirect_uri"),
    ],
)
def test_registration_rejects_unsafe_or_unbounded_metadata(provider, changes, error):
    with pytest.raises(RegistrationError) as caught:
        run(provider.register_client(client(**changes)))
    assert caught.value.error == error
    provider.redis.set.assert_not_awaited()


def test_registration_rate_limit_does_not_store_a_client(provider):
    provider.redis.incr.return_value = 101
    with pytest.raises(RegistrationError):
        run(provider.register_client(client()))
    provider.redis.set.assert_not_awaited()


def parameters(**changes):
    return AuthorizationParams(
        **{
            "redirect_uri": "https://agent.example/callback",
            "redirect_uri_provided_explicitly": True,
            "code_challenge": "challenge",
            "state": "opaque state",
            "scopes": None,
            **changes,
        }
    )


@pytest.mark.parametrize("named", [True, False])
def test_authorization_stores_bounded_consent_request_with_approved_resource(provider, named):
    account = client(client_name="Reader agent" if named else None)
    location = run(provider.authorize(account, parameters()))
    request_id = parse_qs(urlsplit(location).query)["request"][0]
    pending = run(provider.get_record("pending", request_id))
    assert urlsplit(location).path == "/mcp/authorize"
    assert pending["resource"] == RESOURCE
    assert pending["scopes"] == oauth.SCOPES
    assert pending["client_name"] == ("Reader agent" if named else "MCP client")
    assert pending["state"] == "opaque state"
    assert provider.redis.set.await_args.kwargs["ex"] == 600


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
        ({"scopes": ["devfeed:write"]}, "invalid_scope"),
        ({"scopes": ["devfeed:read", "admin"]}, "invalid_scope"),
    ],
)
def test_authorization_rejects_audience_and_scope_escalation(provider, changes, error):
    with pytest.raises(AuthorizeError) as caught:
        run(provider.authorize(client(), parameters(**changes)))
    assert caught.value.error == error
    provider.redis.set.assert_not_awaited()


def test_authorization_rate_limit_is_per_client(provider):
    provider.redis.incr.return_value = 21
    with pytest.raises(AuthorizeError):
        run(provider.authorize(client(), parameters()))
    assert ":client:" in provider.redis.incr.await_args.args[0]
    provider.redis.set.assert_not_awaited()


def code_record(**changes):
    return {
        **parameters(scopes=["devfeed:read"]).model_dump(mode="json"),
        "client_id": "client",
        "grant_id": "grant",
        "expires_at": NOW + 60,
        **changes,
    }


def test_authorization_codes_are_client_bound_and_consumed_once(provider):
    assert run(provider.load_authorization_code(client(), "code")) is None
    record(provider, "code", "code", code_record(client_id="other"))
    assert run(provider.load_authorization_code(client(), "code")) is None
    record(provider, "code", "code", code_record())
    code = run(provider.load_authorization_code(client(), "code"))
    assert isinstance(code, AuthorizationCode)
    provider.issue = AsyncMock(return_value="issued")
    assert run(provider.exchange_authorization_code(client(), code)) == "issued"
    provider.issue.assert_awaited_once_with("grant", "client", ["devfeed:read"])
    with pytest.raises(TokenError):
        run(provider.exchange_authorization_code(client(), code))


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"client_id": "other"}, "invalid_grant"),
        ({"expires_at": NOW}, "invalid_grant"),
        ({"resource": "https://other.example"}, "invalid_scope"),
        ({"scopes": []}, "invalid_scope"),
    ],
)
def test_issuance_validates_grant_before_requesting_atomic_renewal(provider, changes, error):
    record(provider, "grant", "grant", grant(**changes))
    with pytest.raises(TokenError) as caught:
        run(provider.issue("grant", "client", ["devfeed:read"]))
    assert caught.value.error == error
    provider.redis.eval.assert_not_awaited()


@pytest.mark.parametrize("present", [False, True])
def test_missing_or_concurrently_revoked_grants_never_issue_tokens(provider, present):
    if present:
        record(provider, "grant", "grant", grant())
        provider.redis.eval.return_value = None
    with pytest.raises(TokenError):
        run(provider.issue("grant", "client", ["devfeed:read"]))
    provider.redis.set.assert_not_awaited()


@pytest.mark.parametrize("remaining,expected", [(3600, 900), (30, 30)])
def test_access_token_lifetime_is_bounded_by_renewed_grant(provider, remaining, expected):
    value = grant(expires_at=NOW + remaining)
    record(provider, "grant", "grant", value)
    provider.redis.eval.return_value = json.dumps(value)
    issued = run(provider.issue("grant", "client", ["devfeed:read"]))
    assert issued.expires_in == expected
    assert issued.scope == "devfeed:read"
    assert issued.access_token != issued.refresh_token
    access = run(provider.get_record("access", issued.access_token))
    refresh = run(provider.get_record("refresh", issued.refresh_token))
    assert access["expires_at"] == NOW + expected
    assert refresh["expires_at"] == value["expires_at"]
    assert access["subject"] == "reader"
    assert access["resource"] == RESOURCE
    assert provider.redis.expire.await_args.args[1] >= 90 * 86400


@pytest.mark.parametrize(
    "present,owner", [(False, "client"), (False, "other"), (True, "other"), (True, "client")]
)
def test_refresh_loading_is_client_bound_and_replay_revokes_only_its_owner(
    provider, present, owner
):
    record(provider, "grant", "grant", grant())
    record(provider, "used-refresh", "token", token_record(client_id=owner))
    if present:
        record(provider, "refresh", "token", token_record(client_id=owner))
    loaded = run(provider.load_refresh_token(client(), "token"))
    if present and owner == "client":
        assert isinstance(loaded, RefreshToken)
    else:
        assert loaded is None
    assert (oauth.key("grant", "grant") not in provider.redis.records) == (
        not present and owner == "client"
    )


@pytest.mark.parametrize(
    "stage", ["missing", "replay", "missing-grant", "rotation-race", "success"]
)
def test_refresh_exchange_handles_revocation_and_single_use_rotation(provider, stage):
    value = token_record()
    token = RefreshToken.model_validate({**value, "token": "token"})
    if stage != "missing":
        record(provider, "grant", "grant", grant())
    if stage == "replay":
        record(provider, "used-refresh", "token", value)
    elif stage in {"missing-grant", "rotation-race", "success"}:
        record(provider, "refresh", "token", value)
    if stage == "missing-grant":
        provider.redis.records.pop(oauth.key("grant", "grant"))
    provider.redis.eval.return_value = json.dumps(value) if stage == "success" else None
    provider.issue = AsyncMock(return_value="renewed")
    if stage == "success":
        assert run(provider.exchange_refresh_token(client(), token, ["devfeed:read"])) == "renewed"
        provider.issue.assert_awaited_once_with("grant", "client", ["devfeed:read"])
    else:
        with pytest.raises(TokenError):
            run(provider.exchange_refresh_token(client(), token, ["devfeed:read"]))
        provider.issue.assert_not_awaited()
    if stage in {"replay", "rotation-race"}:
        assert oauth.key("grant", "grant") not in provider.redis.records


@pytest.mark.parametrize(
    "stage", ["missing", "expired", "wrong-resource", "missing-grant", "expired-grant", "valid"]
)
def test_access_loading_requires_a_live_grant_and_matching_resource(provider, stage):
    if stage != "missing":
        record(
            provider,
            "access",
            "token",
            token_record(
                expires_at=NOW if stage == "expired" else NOW + 900,
                resource="https://other.example" if stage == "wrong-resource" else RESOURCE,
            ),
        )
    if stage != "missing-grant":
        record(
            provider,
            "grant",
            "grant",
            grant(expires_at=NOW if stage == "expired-grant" else NOW + 3600),
        )
    result = run(provider.load_access_token("token"))
    assert isinstance(result, AccessToken) if stage == "valid" else result is None


@pytest.mark.parametrize("kind,model", [("access", AccessToken), ("refresh", RefreshToken)])
def test_token_revocation_removes_the_connection_without_affecting_other_grants(
    provider, kind, model
):
    token = model.model_validate({**token_record(), "token": "token"})
    record(provider, "grant", "grant", grant())
    record(provider, "grant", "other", grant())
    record(provider, kind, "token", token_record())
    run(provider.revoke_token(token))
    assert oauth.key("grant", "grant") not in provider.redis.records
    assert oauth.key("grant", "other") in provider.redis.records
    provider.redis.zrem.assert_awaited_once_with(oauth.key("connections", "reader"), "grant")
    provider.redis.zrem.reset_mock()
    run(provider.revoke_grant("missing"))
    provider.redis.zrem.assert_not_awaited()
    provider.redis.records.pop(oauth.key(kind, "token"))
    run(provider.revoke_token(token))


@pytest.mark.parametrize(
    "method,path,body,status",
    [
        ("GET", "/token", b"", 200),
        ("POST", "/token", f"resource={RESOURCE}".encode(), 200),
        ("POST", "/token", b"grant_type=refresh_token", 200),
        ("POST", "/token", b"resource=https://other.example", 400),
        ("POST", "/token", f"resource={RESOURCE}&resource={RESOURCE}".encode(), 400),
        ("POST", "/token", b"\xff", 400),
        ("POST", "/register", b"x" * 32768, 200),
        ("POST", "/register", b"x" * 32769, 413),
    ],
)
def test_oauth_transport_bounds_chunked_bodies_and_preserves_downstream_receive(
    method, path, body, status
):
    forwarded = []

    async def app(scope, receive, send):
        forwarded.append(await receive())
        forwarded.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})

    split = len(body) // 2
    messages = [
        {"type": "http.request", "body": body[:split], "more_body": True},
        {"type": "http.request", "body": body[split:], "more_body": False},
        {"type": "http.disconnect"},
    ]
    receive = AsyncMock(side_effect=messages)
    send = AsyncMock()
    scope = {"type": "http", "method": method, "path": path}
    run(oauth.OAuthPolicyMiddleware(app, RESOURCE)(scope, receive, send))
    response = send.await_args_list[0].args[0]
    assert response["status"] == status
    if status != 200:
        assert not forwarded
    elif method == "POST":
        assert forwarded == [
            {"type": "http.request", "body": body, "more_body": False},
            {"type": "http.disconnect"},
        ]


def test_oauth_disconnect_aborts_processing_and_non_http_scopes_pass_through():
    app = AsyncMock()
    middleware = oauth.OAuthPolicyMiddleware(app, RESOURCE)
    run(
        middleware(
            {"type": "http", "method": "POST", "path": "/token"},
            AsyncMock(return_value={"type": "http.disconnect"}),
            AsyncMock(),
        )
    )
    app.assert_not_awaited()
    receive, send = AsyncMock(), AsyncMock()
    run(middleware({"type": "lifespan"}, receive, send))
    app.assert_awaited_once_with({"type": "lifespan"}, receive, send)
