"""Browser consent and grant-management HTTP contracts over Redis read/write boundaries.

Captured Lua arguments describe the requested grant. Lua execution, atomicity,
expiry, concurrency, and token exchange remain integration contracts.
"""

import base64
import hashlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_user_api import mcp, oidc
from devfeed_user_api.auth import UserIdentity
from devfeed_user_api.config import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

NOW = 2_000_000_000
REQUEST = "r" * 43
GRANT = "g" * 43
RESOURCE = "https://mcp.example/mcp"


@pytest.fixture
def agent(monkeypatch):
    settings = Settings(
        _env_file=None,
        base_url="https://reader.example",
        oidc_issuer_url="https://identity.example",
        oidc_client_id="reader",
        oidc_organization_id="readers",
        mcp_resource_url=RESOURCE,
        mcp_issuer_url="https://mcp.example",
        mcp_session_ttl_seconds=3600,
        mcp_session_absolute_ttl_seconds=7200,
    )
    user = UserIdentity(
        user_id=str(uuid.uuid4()),
        issuer=settings.oidc_issuer_url,
        subject="reader",
        organization_id="readers",
        name="Private browser identity",
        email="private@example.test",
        expires_at=NOW + 3600,
        csrf_token="private-csrf",
    )
    pending = {
        "client_id": "agent-client",
        "client_name": "Reader agent",
        "resource": RESOURCE,
        "scopes": ["devfeed:read", "devfeed:write"],
        "redirect_uri": "https://agent.example/callback?flow=reader",
        "state": "opaque browser state",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest())
        .decode()
        .rstrip("="),
    }
    redis = Mock()
    redis.get.return_value = redis.getdel.return_value = json.dumps(pending).encode()
    redis.eval.return_value = 1
    browser = Mock(return_value=user)
    monkeypatch.setattr(mcp, "require_user", browser)
    monkeypatch.setattr(mcp, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp, "get_redis", lambda: redis)
    monkeypatch.setattr(mcp.time, "time", lambda: NOW)
    app = FastAPI()
    app.include_router(mcp.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            redis=redis,
            user=user,
            settings=settings,
            pending=pending,
            browser=browser,
        )


def approval(agent, **payload):
    return agent.client.post(f"/v1/user/mcp/requests/{REQUEST}", json=payload)


def stored_grant(agent, **changes):
    return {
        "identity": {
            field: getattr(agent.user, field)
            for field in ("user_id", "issuer", "subject", "organization_id")
        },
        "client_name": "Reader agent",
        "client_id": "agent-client",
        "resource": RESOURCE,
        "scopes": ["devfeed:read"],
        "policy": oidc.policy_key(agent.settings),
        "expires_at": NOW + 3600,
        "absolute_expires_at": NOW + 7200,
        **changes,
    }


def test_pending_consent_exposes_only_client_request_details(agent):
    response = agent.client.get(f"/v1/user/mcp/requests/{REQUEST}")

    assert response.status_code == 200
    assert response.json() == {
        field: agent.pending[field]
        for field in ("client_name", "scopes", "resource", "redirect_uri")
    }
    assert agent.pending["code_challenge"] not in response.text
    assert "private@example.test" not in response.text
    agent.redis.get.assert_called_once_with(mcp.key("pending", REQUEST))


@pytest.mark.parametrize("request_id", [REQUEST, "malformed"])
def test_missing_or_malformed_consent_requests_have_the_same_not_found_response(agent, request_id):
    agent.redis.get.return_value = None

    response = agent.client.get(f"/v1/user/mcp/requests/{request_id}")

    assert (
        response.status_code == 404 and response.json()["detail"] == "Authorization request expired"
    )
    if request_id != REQUEST:
        agent.redis.get.assert_not_called()


@pytest.mark.parametrize("access", [None, "read-only", "read-write"])
def test_approval_constructs_an_account_bound_grant_with_no_unrequested_scope(agent, access):
    payload = {"approved": True}
    if access is not None:
        payload["access"] = access

    response = approval(agent, **payload)

    assert response.status_code == 200, response.text
    parameters = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert parameters["state"] == [agent.pending["state"]]
    assert parameters["iss"] == ["https://mcp.example"] and parameters["flow"] == ["reader"]
    code = parameters["code"][0]
    assert mcp.TOKEN.fullmatch(code)
    args = agent.redis.eval.call_args.args
    assert args[1] == 3 and args[2] == mcp.key("connections", agent.user.user_id)
    grant, code_record = json.loads(args[6]), json.loads(args[7])
    wanted = ["devfeed:read"] if access == "read-only" else ["devfeed:read", "devfeed:write"]
    assert grant["scopes"] == code_record["scopes"] == wanted
    assert grant["identity"] == stored_grant(agent)["identity"]
    assert grant["policy"] == oidc.policy_key(agent.settings)
    assert grant["expires_at"] == NOW + 3600 and grant["absolute_expires_at"] == NOW + 7200
    assert code_record["subject"] == agent.user.user_id and code_record["expires_at"] == NOW + 300
    assert args[4] == mcp.key("code", code)
    assert args[3] == mcp.key("grant", code_record["grant_id"])
    assert args[-2:] == (3600, 7200)
    assert "private@example.test" not in str(grant) + response.text
    assert "private-csrf" not in str(grant) + response.text
    agent.redis.getdel.assert_called_once_with(mcp.key("pending", REQUEST))


def test_denied_consent_preserves_callback_state_without_creating_a_grant(agent):
    response = approval(agent, approved=False)

    assert response.status_code == 200
    parameters = parse_qs(urlsplit(response.json()["redirect_url"]).query)
    assert parameters["error"] == ["access_denied"] and parameters["state"] == [
        agent.pending["state"]
    ]
    assert "code" not in parameters
    agent.redis.eval.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["expired", "resource", "write-not-requested", "connection-limit"]
)
def test_consent_admission_rejects_expired_unknown_or_unrequested_authority(agent, failure):
    if failure == "expired":
        agent.redis.getdel.return_value = None
    elif failure == "resource":
        agent.pending["resource"] = "https://other.example/mcp"
        agent.redis.getdel.return_value = json.dumps(agent.pending).encode()
    elif failure == "write-not-requested":
        agent.pending["scopes"] = ["devfeed:read"]
        agent.redis.getdel.return_value = json.dumps(agent.pending).encode()
    else:
        agent.redis.eval.return_value = 0

    response = approval(agent, approved=True, access="read-write")

    assert (
        response.status_code
        == {"expired": 404, "resource": 400, "write-not-requested": 400, "connection-limit": 409}[
            failure
        ]
    )
    if failure != "connection-limit":
        agent.redis.eval.assert_not_called()
    assert "redirect_url" not in response.json()


@pytest.mark.parametrize("path", [f"/requests/{REQUEST}", "/connections"])
def test_bearer_authority_cannot_be_used_to_manage_browser_consent(agent, path):
    response = agent.client.get(
        "/v1/user/mcp" + path, headers={"Authorization": "Bearer " + "a" * 43}
    )

    assert response.status_code == 401 and response.json()["detail"] == "Browser sign-in required"
    agent.browser.assert_not_called()
    agent.redis.get.assert_not_called()


def test_consent_storage_errors_are_retryable_and_hide_dependency_details(agent):
    agent.redis.get.side_effect = RedisError("redis://secret@internal")

    response = agent.client.get(f"/v1/user/mcp/requests/{REQUEST}")

    assert response.status_code == 503
    assert response.json()["detail"] == "Agent connections temporarily unavailable"
    assert "secret" not in response.text


@pytest.mark.parametrize("legacy", [False, True])
def test_connection_projection_omits_grant_identity_and_cleans_a_missing_index_record(
    agent, legacy
):
    grant = stored_grant(agent)
    if legacy:
        grant.pop("absolute_expires_at")
    stale = "s" * 43
    agent.redis.zrange.return_value = [GRANT.encode(), stale.encode()]
    agent.redis.get.side_effect = [json.dumps(grant).encode(), None]

    response = agent.client.get("/v1/user/mcp/connections")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": GRANT,
                "client_name": "Reader agent",
                "scopes": ["devfeed:read"],
                "expires_at": NOW + 3600,
                "absolute_expires_at": NOW + (3600 if legacy else 7200),
            }
        ]
    }
    agent.redis.zrem.assert_called_once_with(mcp.key("connections", agent.user.user_id), stale)
    assert agent.user.user_id not in response.text and agent.user.issuer not in response.text


@pytest.mark.parametrize("present", [False, True])
def test_disconnect_is_idempotent_for_an_owned_or_already_removed_grant(agent, present):
    agent.redis.get.return_value = json.dumps(stored_grant(agent)).encode() if present else None

    response = agent.client.delete(f"/v1/user/mcp/connections/{GRANT}")

    assert response.status_code == 204 and response.content == b""
    agent.redis.delete.assert_called_once_with(mcp.key("grant", GRANT))
    agent.redis.zrem.assert_called_once_with(mcp.key("connections", agent.user.user_id), GRANT)


def test_disconnect_cannot_revoke_another_accounts_valid_grant(agent):
    other_identity = {
        **stored_grant(agent)["identity"],
        "user_id": str(uuid.uuid4()),
        "subject": "other-reader",
    }
    agent.redis.get.return_value = json.dumps(stored_grant(agent, identity=other_identity)).encode()

    response = agent.client.delete(f"/v1/user/mcp/connections/{GRANT}")

    assert response.status_code == 404 and response.json()["detail"] == "Agent connection not found"
    agent.redis.delete.assert_not_called()
    agent.redis.zrem.assert_not_called()
