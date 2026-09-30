"""Real Redis + SDK OAuth transport, consent, scopes, isolation and revocation."""

import base64
import hashlib
import json
import os
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from devfeed_mcp.config import Settings
from devfeed_mcp.main import create_app
from devfeed_user_api import auth, mcp, oidc
from devfeed_user_api.config import Settings as UserSettings
from devfeed_user_api.main import create_app as create_user_app
from fastapi.testclient import TestClient
from redis import Redis

pytestmark = pytest.mark.integration

RESOURCE = "https://mcp.example/mcp"
ISSUER = "https://mcp.example"
WEB = "https://reader.example"
USER_ID = "00000000-0000-4000-8000-000000000001"
HEADERS = {"Accept": "application/json, text/event-stream", "Mcp-Protocol-Version": "2025-11-25"}


@pytest.fixture
def flow(monkeypatch):
    url = os.getenv("DEVFEED_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set disposable DEVFEED_TEST_REDIS_URL (database 15)")
    assert urlsplit(url).path == "/15"
    monkeypatch.setenv(
        "DEVFEED_DATABASE_URL", "postgresql+psycopg://test@database.invalid/oauth_test"
    )
    monkeypatch.setenv("DEVFEED_REDIS_URL", url)
    store = Redis.from_url(url)
    store.flushdb()
    settings = UserSettings(
        _env_file=None,
        base_url=WEB,
        oidc_issuer_url="https://identity.example",
        oidc_client_id="reader",
        oidc_organization_id="org",
        mcp_resource_url=RESOURCE,
        mcp_issuer_url=ISSUER,
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_redis", lambda: store)
    monkeypatch.setattr(mcp, "get_redis", lambda: store)
    monkeypatch.setattr(auth, "_record_activity", lambda user: None)
    identity = dict(
        user_id=USER_ID,
        subject="subject-one",
        issuer="https://identity.example",
        organization_id="org",
        name="Reader",
        email=None,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=int(time.time()) + 3600,
    )
    session = secrets.token_urlsafe(32)
    store.set(
        auth.key("session", session),
        json.dumps(
            {
                **identity,
                "absolute_expires_at": int(time.time()) + 7200,
                "policy": oidc.policy_key(settings),
            }
        ),
        ex=3600,
    )
    user = TestClient(create_user_app(), base_url=WEB)
    user.cookies.set(oidc.cookie_name(settings, "session"), session)
    requests = []

    def upstream(request):
        requests.append(request)
        if "/preferences/sources" in request.url.path and request.method == "GET":
            body = {"source_ids": [USER_ID]}
        elif request.url.path.endswith("preferences"):
            body = {"topic_ids": [USER_ID]}
        elif request.url.path.endswith("bookmark"):
            body = {"article_id": USER_ID, **json.loads(request.content)}
        elif request.url.path.endswith("like"):
            body = {"article_id": USER_ID, "likes": 1, **json.loads(request.content)}
        elif request.method == "PUT":
            body = json.loads(request.content)
        else:
            body = {"items": [], "next_cursor": None, "generation": None}
        return httpx.Response(200, json=body)

    app = create_app(
        Settings(
            api_url="https://public.example",
            user_api_url="https://user.example",
            public_url=RESOURCE,
            oauth_issuer_url=ISSUER,
            web_url=WEB,
            redis_url=url,
        ),
        transport=httpx.MockTransport(upstream),
    )
    with TestClient(app, base_url="http://localhost") as client:
        yield SimpleNamespace(
            client=client,
            user=user,
            store=store,
            identity=identity,
            requests=requests,
            settings=settings,
        )
    user.close()
    store.flushdb()
    store.close()


def start(flow, scope="devfeed:read devfeed:write", redirect="http://127.0.0.1:4567/callback"):
    response = flow.client.post(
        "/register",
        json={
            "client_name": "Test agent",
            "redirect_uris": [redirect],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": scope,
        },
    )
    assert response.status_code == 201, response.text
    client_id = response.json()["client_id"]
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = flow.client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect,
            "scope": scope,
            "state": "client-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    return client_id, verifier, request_id, redirect


def approve(flow, pending):
    client_id, verifier, request_id, redirect = pending
    response = flow.user.post(
        f"/v1/user/mcp/requests/{request_id}",
        json={"approved": True},
        headers={"Origin": WEB, "X-CSRF-Token": flow.identity["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    location = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert location["state"] == ["client-state"]
    assert location["iss"] == [ISSUER]
    return dict(
        grant_type="authorization_code",
        client_id=client_id,
        code=location["code"][0],
        code_verifier=verifier,
        redirect_uri=redirect,
        resource=RESOURCE,
    )


def tokens(flow, scope="devfeed:read devfeed:write"):
    form = approve(flow, start(flow, scope))
    response = flow.client.post("/token", data=form)
    assert response.status_code == 200, response.text
    return response.json(), form


def call(flow, token, name, arguments=None):
    return flow.client.post(
        "/mcp",
        headers={**HEADERS, "Authorization": "Bearer " + token},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )


def test_shared_endpoint_public_access_and_invalid_bearer(flow):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    response = flow.client.post("/mcp", headers=HEADERS, json=request)
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 14
    response = flow.client.post(
        "/mcp", headers={**HEADERS, "Authorization": "Bearer invalid"}, json=request
    )
    assert response.status_code == 401
    assert flow.client.post("/personal/mcp", headers=HEADERS, json=request).status_code == 404
    response = flow.client.post("/mcp", headers=HEADERS, content=b"x" * 32769)
    assert response.status_code == 413


def test_browser_visits_open_reader_with_oauth_enabled(flow):
    response = flow.client.get("/mcp", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == WEB + "/mcp"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("accept", ["application/json", "*/*", "text/event-stream"])
def test_login_discovery_gets_json_challenge_without_opening_sse(flow, method, accept):
    response = flow.client.request(method, "/mcp", headers={"Accept": accept})
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/json"
    metadata_url = (
        response.headers["www-authenticate"].split('resource_metadata="')[1].split('"')[0]
    )
    metadata = flow.client.get(metadata_url)
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == RESOURCE
    assert metadata.json()["authorization_servers"] == [ISSUER]
    if method == "GET":
        assert response.json()["error"] == "invalid_token"


def test_discovery_and_browser_consent(flow):
    response = flow.client.post(
        "/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 14
    response = flow.client.post(
        "/mcp",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_my_feed", "arguments": {}},
        },
    )
    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]
    metadata = flow.client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]
    assert metadata["scopes_supported"] == ["devfeed:read", "devfeed:write"]
    metadata = flow.client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert "offline_access" in metadata["scopes_supported"]
    client_id, verifier, request_id, redirect = start(flow)
    response = flow.user.get(f"/v1/user/mcp/requests/{request_id}")
    assert response.json()["client_name"] == "Test agent"
    assert response.json()["scopes"] == ["devfeed:read", "devfeed:write"]
    assert (
        flow.user.post(f"/v1/user/mcp/requests/{request_id}", json={"approved": True}).status_code
        == 403
    )
    token, form = tokens(flow)
    response = flow.client.post(
        "/mcp",
        headers={**HEADERS, "Authorization": "Bearer " + token["access_token"]},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    tools = response.json()["result"]["tools"]
    assert len(tools) == 14
    assert all(
        t["annotations"]["readOnlyHint"] == (not t["name"].startswith("set_")) for t in tools
    )
    assert flow.client.post("/token", data=form).status_code == 400


def test_pkce_redirect_and_resource_validation(flow):
    form = approve(flow, start(flow))
    assert flow.client.post("/token", data={**form, "code_verifier": "wrong"}).status_code == 400
    assert (
        flow.client.post(
            "/token", data={**form, "redirect_uri": "https://evil.example/callback"}
        ).status_code
        == 400
    )
    assert flow.client.post("/token", data=form).status_code == 200
    response = flow.client.post(
        "/register",
        json={
            "redirect_uris": ["http://evil.example/callback"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "name,args,path",
    [
        ("get_my_feed", {}, "/v1/user/feed"),
        ("list_my_bookmarks", {}, "/v1/user/bookmarks"),
        ("list_my_followed_topics", {}, "/v1/user/preferences"),
        ("list_my_followed_sources", {}, "/v1/user/preferences/sources"),
        (
            "set_bookmark",
            {"article_id": USER_ID, "bookmarked": True},
            f"/v1/user/articles/{USER_ID}/bookmark",
        ),
        (
            "set_topic_follow",
            {"topic_id": USER_ID, "followed": False},
            f"/v1/user/preferences/topics/{USER_ID}",
        ),
        (
            "set_source_follow",
            {"source_id": USER_ID, "followed": True},
            f"/v1/user/preferences/sources/{USER_ID}",
        ),
        (
            "set_article_like",
            {"article_id": USER_ID, "liked": True},
            f"/v1/user/articles/{USER_ID}/like",
        ),
    ],
)
def test_all_account_tools_use_fixed_user_api_and_scoped_token(flow, name, args, path):
    token, _ = tokens(flow)
    response = call(flow, token["access_token"], name, args)
    assert response.status_code == 200, response.text
    assert not response.json()["result"].get("isError"), response.text
    request = flow.requests[-1]
    assert request.url.host == "user.example"
    assert request.url.path == path
    assert request.headers["authorization"] == "Bearer " + token["access_token"]
    assert "cookie" not in request.headers
    assert "user_id" not in request.url.params


def test_read_only_grant_refresh_rotation_and_immediate_disconnect(flow):
    token, form = tokens(flow, "devfeed:read")
    response = call(
        flow, token["access_token"], "set_bookmark", {"article_id": USER_ID, "bookmarked": True}
    )
    assert response.json()["result"]["isError"]
    assert not flow.requests
    refresh = dict(
        grant_type="refresh_token",
        client_id=form["client_id"],
        refresh_token=token["refresh_token"],
        resource=RESOURCE,
    )
    response = flow.client.post("/token", data=refresh)
    assert response.status_code == 200, response.text
    new_token = response.json()
    connection = flow.user.get("/v1/user/mcp/connections").json()["items"][0]
    response = flow.user.delete(
        f"/v1/user/mcp/connections/{connection['id']}",
        headers={"Origin": WEB, "X-CSRF-Token": flow.identity["csrf_token"]},
    )
    assert response.status_code == 204, response.text
    for access in [token["access_token"], new_token["access_token"]]:
        assert call(flow, access, "get_my_feed").status_code == 401
    assert (
        flow.client.post(
            "/token", data={**refresh, "refresh_token": new_token["refresh_token"]}
        ).status_code
        == 400
    )


def test_user_api_binds_identity_and_denies_other_endpoints(flow, monkeypatch):
    from uuid import UUID

    from devfeed_core import db
    from fastapi import HTTPException
    from starlette.requests import Request

    token, _ = tokens(flow)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalar(self, statement):
            return UUID(USER_ID)

    monkeypatch.setattr(db, "session_factory", lambda: Session)

    def request(path, method="GET"):
        return Request(
            {
                "type": "http",
                "method": method,
                "path": path,
                "headers": [(b"authorization", ("Bearer " + token["access_token"]).encode())],
            }
        )

    assert auth.require_user(request("/v1/user/bookmarks")).user_id == USER_ID
    for path, method in [
        ("/v1/user/settings/profile", "GET"),
        ("/v1/user/articles/" + USER_ID + "/open", "POST"),
        ("/v1/user/mcp/connections", "GET"),
    ]:
        with pytest.raises(HTTPException) as error:
            auth.require_user(request(path, method))
        assert error.value.status_code == 403
    monkeypatch.setattr(Session, "scalar", lambda self, statement: None)
    with pytest.raises(HTTPException) as error:
        auth.require_user(request("/v1/user/bookmarks"))
    assert error.value.status_code == 401


def test_rejects_wrong_resource_at_token_endpoint_and_scope_escalation(flow):
    form = approve(flow, start(flow, "devfeed:read"))
    response = flow.client.post("/token", data={**form, "resource": "https://other.example/mcp"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target"
    token = flow.client.post("/token", data=form).json()
    response = flow.client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": form["client_id"],
            "refresh_token": token["refresh_token"],
            "scope": "devfeed:read devfeed:write",
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


def test_cancellation_expiry_and_other_account_cannot_disconnect(flow):
    pending = start(flow)
    request_id = pending[2]
    response = flow.user.post(
        f"/v1/user/mcp/requests/{request_id}",
        json={"approved": False},
        headers={"Origin": WEB, "X-CSRF-Token": flow.identity["csrf_token"]},
    )
    assert parse_qs(urlsplit(response.json()["redirect_url"]).query)["error"] == ["access_denied"]
    assert flow.user.get("/v1/user/mcp/connections").json() == {"items": []}
    assert flow.user.get(f"/v1/user/mcp/requests/{request_id}").status_code == 404
    token, _ = tokens(flow)
    connection = flow.user.get("/v1/user/mcp/connections").json()["items"][0]
    grant_key = mcp.key("grant", connection["id"])
    grant = json.loads(flow.store.get(grant_key))
    grant["identity"]["user_id"] = "00000000-0000-4000-8000-000000000002"
    flow.store.set(grant_key, json.dumps(grant), ex=300)
    response = flow.user.delete(
        f"/v1/user/mcp/connections/{connection['id']}",
        headers={"Origin": WEB, "X-CSRF-Token": flow.identity["csrf_token"]},
    )
    assert response.status_code == 404
    access_key = mcp.key("access", token["access_token"])
    access = json.loads(flow.store.get(access_key))
    access["expires_at"] = int(time.time()) - 1
    flow.store.set(access_key, json.dumps(access), ex=300)
    assert call(flow, token["access_token"], "get_my_feed").status_code == 401


def test_consent_grant_limit_is_enforced_atomically(flow):
    index = mcp.key("connections", USER_ID)
    flow.store.zadd(index, {f"existing-{number}": int(time.time()) + 3600 for number in range(20)})
    request_id = start(flow)[2]
    response = flow.user.post(
        f"/v1/user/mcp/requests/{request_id}",
        json={"approved": True},
        headers={"Origin": WEB, "X-CSRF-Token": flow.identity["csrf_token"]},
    )
    assert response.status_code == 409
    assert flow.store.zcard(index) == 20
    assert not list(flow.store.scan_iter("devfeed:mcp:code:*"))


def test_refresh_replay_revokes_the_entire_connection(flow):
    token, form = tokens(flow)
    refresh = dict(
        grant_type="refresh_token",
        client_id=form["client_id"],
        refresh_token=token["refresh_token"],
        resource=RESOURCE,
    )
    renewed = flow.client.post("/token", data=refresh).json()
    assert flow.client.post("/token", data=refresh).status_code == 400
    assert call(flow, renewed["access_token"], "get_my_feed").status_code == 401
    assert call(flow, token["access_token"], "get_my_feed").status_code == 401
    assert flow.user.get("/v1/user/mcp/connections").json() == {"items": []}
    assert (
        flow.client.post(
            "/token", data={**refresh, "refresh_token": renewed["refresh_token"]}
        ).status_code
        == 400
    )


def test_active_refresh_extends_idle_expiry_but_not_absolute_lifetime(flow, monkeypatch):
    from devfeed_mcp import oauth

    token, form = tokens(flow)
    assert token["expires_in"] == 900
    grant_id = flow.user.get("/v1/user/mcp/connections").json()["items"][0]["id"]
    initial = json.loads(flow.store.get(mcp.key("grant", grant_id)))
    started = initial["absolute_expires_at"] - 90 * 86400
    for day in (20, 40, 60, 80):
        now = started + day * 86400
        monkeypatch.setattr(oauth, "time", SimpleNamespace(time=lambda now=now: now))
        response = flow.client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": form["client_id"],
                "refresh_token": token["refresh_token"],
                "resource": RESOURCE,
            },
        )
        assert response.status_code == 200, response.text
        token = response.json()
        grant = json.loads(flow.store.get(mcp.key("grant", grant_id)))
        assert grant["expires_at"] == min(now + 30 * 86400, initial["absolute_expires_at"])
        assert grant["absolute_expires_at"] == initial["absolute_expires_at"]
        assert flow.store.zscore(mcp.key("connections", USER_ID), grant_id) == grant["expires_at"]
    monkeypatch.setattr(
        oauth, "time", SimpleNamespace(time=lambda: initial["absolute_expires_at"] + 1)
    )
    response = flow.client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": form["client_id"],
            "refresh_token": token["refresh_token"],
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 400


def test_expired_idle_grant_and_revoked_grant_cannot_be_renewed(flow):
    token, form = tokens(flow)
    connection = flow.user.get("/v1/user/mcp/connections").json()["items"][0]
    grant_key = mcp.key("grant", connection["id"])
    grant = json.loads(flow.store.get(grant_key))
    grant["expires_at"] = int(time.time()) - 1
    flow.store.set(grant_key, json.dumps(grant), ex=300)
    response = flow.client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": form["client_id"],
            "refresh_token": token["refresh_token"],
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 400
    assert json.loads(flow.store.get(grant_key))["expires_at"] == grant["expires_at"]
    assert call(flow, token["access_token"], "get_my_feed").status_code == 401
