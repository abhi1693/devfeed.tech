"""Agent permissions follow matched operations while browser sessions keep their own safeguards."""

import json
import uuid
from types import SimpleNamespace as NS

import pytest
from devfeed_core import db
from devfeed_http.agent_permissions import AGENT_POLICY_EXTENSION, AgentAccessPolicy
from devfeed_user_api import auth, mcp, oidc
from devfeed_user_api.auth import BrowserUser, User
from devfeed_user_api.config import Settings
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.dialects import postgresql

WEB = "https://reader.example"
RESOURCE = "https://mcp.example/mcp"
NOW = 2_000_000_000
USER_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
TOKEN = "a" * 43
BROWSER_TOKEN = "b" * 43
CSRF = "c" * 43
READ = "devfeed:read"
WRITE = "devfeed:write"


@pytest.fixture
def identity(monkeypatch):
    settings = Settings(
        _env_file=None,
        base_url=WEB,
        oidc_issuer_url="https://identity.example",
        oidc_client_id="reader",
        oidc_organization_id="organization",
        mcp_resource_url=RESOURCE,
        mcp_issuer_url="https://mcp.example",
    )
    user = {
        "user_id": USER_ID,
        "issuer": "https://identity.example",
        "subject": "subject",
        "organization_id": "organization",
    }
    access = {
        "grant_id": "opaque-grant",
        "scopes": [READ, WRITE],
        "resource": RESOURCE,
        "expires_at": NOW + 900,
    }
    grant = {
        "identity": user,
        "scopes": [READ, WRITE],
        "resource": RESOURCE,
        "expires_at": NOW + 3600,
        "policy": oidc.policy_key(settings),
    }
    browser = {
        **user,
        "name": "Reader",
        "email": None,
        "csrf_token": CSRF,
        "expires_at": NOW + 3600,
        "absolute_expires_at": NOW + 7200,
        "policy": oidc.policy_key(settings),
    }
    records = {
        mcp.key("access", TOKEN): access,
        mcp.key("grant", "opaque-grant"): grant,
        auth.key("session", BROWSER_TOKEN): browser,
    }
    state = NS(
        settings=settings,
        user=user,
        access=access,
        grant=grant,
        records=records,
        queries=[],
        reads=[],
        account_available=True,
    )

    class Store:
        def get(self, key):
            state.reads.append(key)
            value = records.get(key)
            return json.dumps(value).encode() if value is not None else None

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def scalar(self, statement):
            state.queries.append(statement)
            return uuid.UUID(USER_ID) if state.account_available else None

    store = Store()
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_redis", lambda: store)
    monkeypatch.setattr(mcp, "get_redis", lambda: store)
    monkeypatch.setattr(auth, "_record_activity", lambda _user: None)
    monkeypatch.setattr(auth, "time", NS(time=lambda: NOW))
    monkeypatch.setattr(mcp, "time", NS(time=lambda: NOW))
    monkeypatch.setattr(db, "session_factory", lambda: Session)
    state.store = store
    return state


def app():
    service, leaf = FastAPI(), APIRouter(prefix="/renamed")

    @leaf.get("/read", openapi_extra=AgentAccessPolicy(scope=READ).openapi_extra())
    def read(user: User):
        return {"user_id": user.user_id, "csrf_token": user.csrf_token}

    @leaf.put("/items/{resource_id}", openapi_extra=AgentAccessPolicy(scope=WRITE).openapi_extra())
    def write(resource_id: uuid.UUID, user: User):
        return {"resource_id": str(resource_id), "user_id": user.user_id}

    @leaf.get("/same", openapi_extra=AgentAccessPolicy(scope=READ).openapi_extra())
    def same_read(user: User):
        return {"user_id": user.user_id}

    @leaf.put("/same")
    def same_write(user: User):
        return {"user_id": user.user_id}

    @leaf.get("/settings/profile")
    def sensitive(user: User):
        return {"user_id": user.user_id}

    @leaf.get("/browser")
    def browser(user: BrowserUser):
        return {"user_id": user.user_id, "csrf_token": user.csrf_token}

    @leaf.put("/browser")
    def browser_write(user: BrowserUser):
        return {"user_id": user.user_id, "csrf_token": user.csrf_token}

    parent = APIRouter(prefix="/nested")
    parent.include_router(leaf)
    service.include_router(parent, prefix="/v9")
    service.include_router(leaf, prefix="/alias")
    return service


def bearer(**headers):
    return {"Authorization": "Bearer " + TOKEN, **headers}


@pytest.mark.parametrize("value", [None, "read", "devfeed:admin", True, 1, [READ]])
def test_agent_policy_rejects_invalid_scope_values(value):
    with pytest.raises(ValidationError):
        AgentAccessPolicy(scope=value)


def test_agent_policy_is_explicit_strict_frozen_and_independent_of_url_spelling():
    policy = AgentAccessPolicy(scope=READ)
    assert policy.openapi_extra() == {AGENT_POLICY_EXTENSION: {"scope": READ}}
    with pytest.raises(ValidationError):
        AgentAccessPolicy(scope=READ, path="/feed")
    with pytest.raises(ValidationError):
        policy.scope = WRITE


@pytest.mark.parametrize(
    "declaration", [None, {}, {"scope": "devfeed:admin"}, {"scope": READ, "path": "/feed"}]
)
def test_missing_or_invalid_matched_metadata_does_not_grant_agent_access(declaration):
    route = APIRoute(
        "/arbitrary", lambda: None, openapi_extra={AGENT_POLICY_EXTENSION: declaration}
    )
    request = Request({"type": "http", "method": "GET", "path": "/v1/user/feed", "route": route})
    assert AgentAccessPolicy.from_request(request) is None


@pytest.mark.parametrize(
    "route", [None, NS(openapi_extra={AGENT_POLICY_EXTENSION: {"scope": READ}})]
)
def test_path_headers_and_untrusted_route_like_objects_cannot_grant_permissions(route):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/user/feed",
            "route": route,
            "endpoint": NS(openapi_extra={AGENT_POLICY_EXTENSION: {"scope": READ}}),
            "headers": [(b"x-agent-scope", READ.encode())],
        }
    )
    assert AgentAccessPolicy.from_request(request) is None


@pytest.mark.parametrize("prefix", ["/v9/nested", "/alias"])
def test_marked_permissions_follow_nested_and_reused_router_aliases(identity, prefix):
    with TestClient(app(), base_url=WEB) as client:
        read = client.get(prefix + "/renamed/read", headers=bearer())
        assert read.status_code == 200 and read.json() == {"user_id": USER_ID, "csrf_token": ""}
        write = client.put(prefix + "/renamed/items/" + USER_ID, headers=bearer())
        assert write.status_code == 200 and write.json() == {
            "resource_id": USER_ID,
            "user_id": USER_ID,
        }
    assert len(identity.queries) == 2


def test_marked_permission_follows_mounted_app_and_external_root(identity):
    outer = FastAPI()
    outer.mount("/proxy", app())
    with TestClient(outer, base_url=WEB, root_path="/edge") as client:
        result = client.get("/edge/proxy/alias/renamed/read", headers=bearer())
    assert result.status_code == 200 and result.json()["user_id"] == USER_ID


@pytest.mark.parametrize("value", [USER_ID, uuid.UUID(USER_ID).hex, USER_ID.upper()])
def test_agent_write_uses_native_uuid_validation_for_canonical_compact_and_uppercase_ids(
    identity, value
):
    with TestClient(app(), base_url=WEB) as client:
        result = client.put("/alias/renamed/items/" + value, headers=bearer())
    assert result.status_code == 200 and result.json()["resource_id"] == USER_ID


def test_agent_permission_does_not_replace_native_path_validation_or_method_routing(identity):
    with TestClient(app(), base_url=WEB) as client:
        wrong_method = client.post("/alias/renamed/same", headers=bearer())
        assert wrong_method.status_code == 405 and not identity.queries
        invalid_id = client.put("/alias/renamed/items/not-a-uuid", headers=bearer())
    assert invalid_id.status_code == 422
    assert invalid_id.json()["detail"][0]["loc"] == ["path", "resource_id"]


def test_bare_request_scope_with_a_previously_allowed_path_is_denied(identity):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/user/feed",
            "headers": [(b"authorization", ("Bearer " + TOKEN).encode())],
        }
    )
    with pytest.raises(HTTPException) as denied:
        auth.require_user(request)
    assert denied.value.status_code == 403 and not identity.queries


@pytest.mark.parametrize(
    "needed,path,method", [(READ, "/read", "GET"), (WRITE, "/items/" + USER_ID, "PUT")]
)
@pytest.mark.parametrize("record_name", ["access", "grant"])
def test_both_access_token_and_grant_must_independently_include_the_marked_permission(
    identity, needed, path, method, record_name
):
    getattr(identity, record_name)["scopes"] = [WRITE if needed == READ else READ]
    with TestClient(app(), base_url=WEB) as client:
        result = client.request(method, "/alias/renamed" + path, headers=bearer())
    assert result.status_code == 403 and result.json() == {"detail": "Agent permission denied"}
    assert not identity.queries


def test_unmarked_operation_at_the_same_path_stays_denied_with_both_permissions(identity):
    with TestClient(app(), base_url=WEB) as client:
        assert client.get("/alias/renamed/same", headers=bearer()).status_code == 200
        result = client.put("/alias/renamed/same", headers=bearer())
    assert result.status_code == 403 and len(identity.queries) == 1


@pytest.mark.parametrize(
    "declaration", [None, {"scope": "devfeed:admin"}, {"scope": READ, "extra": True}]
)
def test_invalid_declared_permission_is_denied_before_identity_database_lookup(
    identity, declaration
):
    service = FastAPI()

    @service.get("/marked", openapi_extra={AGENT_POLICY_EXTENSION: declaration})
    def endpoint(user: User):
        pytest.fail("Invalid agent policy reached its endpoint")

    with TestClient(service, base_url=WEB) as client:
        result = client.get("/marked", headers=bearer())
    assert result.status_code == 403 and not identity.queries


def test_unmarked_sensitive_user_endpoint_ignores_spoofed_agent_scope_headers(identity):
    with TestClient(app(), base_url=WEB) as client:
        result = client.get(
            "/alias/renamed/settings/profile",
            headers=bearer(
                **{
                    "X-Agent-Scope": READ,
                    "X-DevFeed-Agent-Scope": READ,
                    "X-Agent-Permission": WRITE,
                    "Cookie": "__Host-devfeed_user_session=" + BROWSER_TOKEN,
                }
            ),
        )
    assert result.status_code == 403 and not identity.queries
    assert identity.reads == [mcp.key("access", TOKEN), mcp.key("grant", "opaque-grant")]


@pytest.mark.parametrize(
    "stage",
    [
        "access-missing",
        "access-expired",
        "grant-revoked",
        "grant-expired",
        "access-resource",
        "grant-resource",
        "changed-client",
    ],
)
def test_matched_permissions_do_not_override_token_grant_resource_or_client_revocation(
    identity, stage
):
    if stage == "access-missing":
        identity.records.pop(mcp.key("access", TOKEN))
    elif stage == "access-expired":
        identity.access["expires_at"] = NOW
    elif stage == "grant-revoked":
        identity.records.pop(mcp.key("grant", "opaque-grant"))
    elif stage == "grant-expired":
        identity.grant["expires_at"] = NOW
    elif stage == "access-resource":
        identity.access["resource"] = "https://other.example/mcp"
    elif stage == "grant-resource":
        identity.grant["resource"] = "https://other.example/mcp"
    else:
        identity.settings.oidc_client_id = "changed-client"
    with TestClient(app(), base_url=WEB) as client:
        result = client.get("/alias/renamed/read", headers=bearer())
    assert result.status_code == 401 and result.headers["www-authenticate"] == "Bearer"
    assert not identity.queries


def test_revocation_is_checked_again_for_every_request(identity):
    with TestClient(app(), base_url=WEB) as client:
        assert client.get("/alias/renamed/read", headers=bearer()).status_code == 200
        identity.records.pop(mcp.key("grant", "opaque-grant"))
        assert client.get("/alias/renamed/read", headers=bearer()).status_code == 401
    assert len(identity.queries) == 1


def test_agent_identity_lookup_remains_fenced_to_account_issuer_subject_and_organization(identity):
    with TestClient(app(), base_url=WEB) as client:
        assert client.get("/alias/renamed/read", headers=bearer()).status_code == 200
        identity.account_available = False
        result = client.get("/alias/renamed/read", headers=bearer())
    assert result.status_code == 401 and result.json() == {"detail": "User account unavailable"}
    values = identity.queries[0].compile(dialect=postgresql.dialect()).params
    assert set(values.values()) == {
        uuid.UUID(USER_ID),
        "https://identity.example",
        "subject",
        "organization",
    }


def test_redis_outage_remains_a_controlled_authentication_failure(identity, monkeypatch):
    monkeypatch.setattr(
        identity.store, "get", lambda _key: (_ for _ in ()).throw(RedisError("private address"))
    )
    with TestClient(app(), base_url=WEB) as client:
        result = client.get("/alias/renamed/read", headers=bearer())
    assert result.status_code == 503 and "private" not in result.text and not identity.queries


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_browser_only_dependencies_keep_cookie_identity_and_csrf_when_a_bearer_is_present(
    identity, method
):
    browser_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    identity.records[auth.key("session", BROWSER_TOKEN)]["user_id"] = browser_id
    headers = {
        "Cookie": "__Host-devfeed_user_session=" + BROWSER_TOKEN,
        "Origin": WEB,
        "X-CSRF-Token": CSRF,
    }
    with TestClient(app(), base_url=WEB) as client:
        ordinary = client.request(method, "/alias/renamed/browser", headers=headers)
        assert ordinary.status_code == 200
        result = client.request(method, "/alias/renamed/browser", headers={**headers, **bearer()})
    assert result.status_code == 200
    assert result.json() == ordinary.json() == {"user_id": browser_id, "csrf_token": CSRF}
    assert identity.reads == [auth.key("session", BROWSER_TOKEN)] * 2
    assert not identity.queries


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_browser_only_dependencies_cannot_use_an_agent_token_without_a_browser_cookie(
    identity, method
):
    with TestClient(app(), base_url=WEB) as client:
        result = client.request(
            method, "/alias/renamed/browser", headers=bearer(Origin=WEB, **{"X-CSRF-Token": CSRF})
        )
    assert result.status_code == 401 and result.json() == {"detail": "User sign-in required"}
    assert not identity.reads and not identity.queries


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"Origin": "https://untrusted.example", "X-CSRF-Token": CSRF},
        {"Origin": WEB, "X-CSRF-Token": "x" * 43},
    ],
)
def test_browser_only_writes_keep_csrf_and_origin_checks_even_with_a_valid_agent_token(
    identity, change
):
    headers = bearer(Cookie="__Host-devfeed_user_session=" + BROWSER_TOKEN, **change)
    with TestClient(app(), base_url=WEB) as client:
        result = client.put("/alias/renamed/browser", headers=headers)
    assert result.status_code == 403
    assert result.json() == {"detail": "Invalid request origin or CSRF token"}
    assert identity.reads == [auth.key("session", BROWSER_TOKEN)]
    assert not identity.queries


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"Origin": "https://untrusted.example", "X-CSRF-Token": CSRF},
        {"Origin": WEB, "X-CSRF-Token": "x" * 43},
    ],
)
def test_marking_an_agent_write_does_not_disable_browser_csrf_or_origin_checks(identity, change):
    headers = {"Cookie": "__Host-devfeed_user_session=" + BROWSER_TOKEN, **change}
    with TestClient(app(), base_url=WEB) as client:
        result = client.put("/alias/renamed/items/" + USER_ID, headers=headers)
        assert result.status_code == 403
        valid = client.put(
            "/alias/renamed/items/" + USER_ID,
            headers={**headers, "Origin": WEB, "X-CSRF-Token": CSRF},
        )
    assert valid.status_code == 200 and not identity.queries
