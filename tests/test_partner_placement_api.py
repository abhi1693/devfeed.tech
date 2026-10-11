"""Delivery HTTP boundaries: strict context, anonymous output and browser-only personalization."""

import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_api import partner_placements as public_api
from devfeed_api.dependencies import get_session as public_session
from devfeed_api.main import create_app as public_app
from devfeed_core.config import get_settings
from devfeed_http.partner_placements import PlacementQuery
from devfeed_user_api import auth
from devfeed_user_api import main as user_main
from devfeed_user_api import partner_placements as private_api
from devfeed_user_api.config import Settings as UserSettings
from devfeed_user_api.dependencies import get_session as private_session
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

KEY = "disposable-partner-placement-key-32-bytes"
ORIGIN = "https://user.example"
USER_ID = str(uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
ARTICLE_ID = str(uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
CSRF = "c" * 43
COOKIE = "s" * 43


def make_client(monkeypatch, private=False):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    module = private_api if private else public_api
    session, limiter, deliver = Mock(), Mock(return_value=0), Mock(return_value=[])
    monkeypatch.setattr(module, "consume_rate_limits", limiter)
    monkeypatch.setattr(module, "deliver_placements", deliver)
    monkeypatch.setattr(module, "get_redis" if private else "get_rate_limit_redis", Mock())
    headers = {}
    store = Mock()
    if private:
        settings = UserSettings(
            _env_file=None,
            base_url=ORIGIN,
            oidc_issuer_url="https://identity.example",
            oidc_client_id="reader-client",
            oidc_organization_id="organization",
        )
        monkeypatch.setattr(auth, "get_settings", lambda: settings)
        monkeypatch.setattr(user_main, "get_settings", lambda: settings)
        monkeypatch.setattr(auth, "get_redis", lambda: store)
        monkeypatch.setattr(auth, "_record_activity", Mock())
        store.get.return_value = json.dumps(
            {
                "subject": "reader",
                "issuer": settings.oidc_issuer_url,
                "organization_id": settings.oidc_organization_id,
                "user_id": USER_ID,
                "expires_at": int(time.time()) + 3600,
                "absolute_expires_at": int(time.time()) + 7200,
                "csrf_token": CSRF,
                "policy": auth.oidc.policy_key(settings),
            }
        ).encode()
        headers = {
            "Cookie": f"__Host-devfeed_user_session={COOKIE}",
            "Origin": ORIGIN,
            "X-CSRF-Token": CSRF,
        }
    app = user_main.create_app() if private else public_app()
    app.dependency_overrides[private_session if private else public_session] = lambda: session
    return SimpleNamespace(
        client=TestClient(app, base_url=ORIGIN),
        app=app,
        path="/v1/user/partner-placements/query" if private else "/v1/partner-placements/query",
        private=private,
        session=session,
        limiter=limiter,
        deliver=deliver,
        store=store,
        headers=headers,
    )


@pytest.fixture(params=[False, True], ids=["public", "personalized"])
def placement_client(monkeypatch, request):
    return make_client(monkeypatch, request.param)


def test_disabled_delivery_returns_empty_without_storage_or_rate_limit(
    placement_client, monkeypatch
):
    state = placement_client
    monkeypatch.setattr(get_settings(), "partner_tracking_key", None)
    response = state.client.post(state.path, headers=state.headers, json={"surface": "feed"})
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert response.headers["cache-control"] == "no-store"
    state.deliver.assert_not_called()
    state.limiter.assert_not_called()
    state.session.commit.assert_not_called()


def test_delivery_identity_comes_only_from_the_browser_session(placement_client):
    state = placement_client
    response = state.client.post(
        state.path,
        headers={**state.headers, "Authorization": "Bearer ignored-provider-token"},
        json={"surface": "article", "article_id": ARTICLE_ID},
    )
    assert response.status_code == 200
    assert response.json() == {"items": []}
    args, kwargs = state.deliver.call_args
    assert args[0] is state.session
    assert args[1] == PlacementQuery(surface="article", article_id=ARTICLE_ID)
    assert args[2] == KEY
    assert kwargs == ({"user_id": USER_ID} if state.private else {})
    state.session.commit.assert_called_once_with()
    state.session.rollback.assert_not_called()
    assert "Bearer" not in response.text and USER_ID not in response.text


@pytest.mark.parametrize("field", ["user_id", "partner_id", "campaign_id", "interests", "receipt"])
def test_identity_and_commercial_claims_cannot_be_supplied_by_readers(placement_client, field):
    state = placement_client
    response = state.client.post(
        state.path, headers=state.headers, json={"surface": "feed", field: USER_ID}
    )
    assert response.status_code == 422
    state.deliver.assert_not_called()
    state.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"surface": "unknown"},
        {"surface": "article"},
        {"surface": "article", "article_id": "invalid"},
        {"surface": "topic", "article_id": ARTICLE_ID},
        {"surface": "feed", "topic_id": ARTICLE_ID},
        {"surface": "article", "article_id": ARTICLE_ID, "topic_id": ARTICLE_ID},
        {"surface": "feed", "exclude_product_ids": [ARTICLE_ID] * 101},
    ],
)
def test_context_and_exclusion_schema_rejects_ambiguous_requests(placement_client, payload):
    state = placement_client
    assert state.client.post(state.path, headers=state.headers, json=payload).status_code == 422
    state.deliver.assert_not_called()
    state.session.commit.assert_not_called()


@pytest.mark.parametrize("content,status", [("{", 422), (" " * 8193, 413)])
def test_malformed_and_oversized_requests_never_issue_receipts(placement_client, content, status):
    state = placement_client
    response = state.client.post(
        state.path,
        headers={**state.headers, "Content-Type": "application/json"},
        content=content,
    )
    assert response.status_code == status
    state.deliver.assert_not_called()
    state.limiter.assert_not_called()
    state.session.commit.assert_not_called()
    if status == 413:
        assert response.headers["cache-control"] == "no-store"


def test_byte_limit_accepts_exact_boundary_and_rejects_understated_length(placement_client):
    state = placement_client
    body = json.dumps({"surface": "feed"}).ljust(8192)
    response = state.client.post(
        state.path,
        headers={**state.headers, "Content-Type": "application/json"},
        content=body,
    )
    assert response.status_code == 200
    state.deliver.reset_mock()
    response = state.client.post(
        state.path,
        headers={
            **state.headers,
            "Content-Type": "application/json",
            "Content-Length": "1",
        },
        content=body + " ",
    )
    assert response.status_code == 413
    state.deliver.assert_not_called()


@pytest.mark.parametrize(
    "failure,status", [("limited", 429), ("redis", 503), ("database", 503), ("commit", 503)]
)
def test_delivery_failures_rollback_and_do_not_expose_private_details(
    placement_client, failure, status
):
    state = placement_client
    if failure == "limited":
        state.limiter.return_value = 37
    elif failure == "redis":
        state.limiter.side_effect = RedisError("private infrastructure details")
    elif failure == "database":
        state.deliver.side_effect = SQLAlchemyError("private query details")
    else:
        state.session.commit.side_effect = SQLAlchemyError("private transaction details")
    response = state.client.post(state.path, headers=state.headers, json={"surface": "feed"})
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
    assert state.session.rollback.call_count == int(failure != "limited")
    if failure in {"redis", "limited"}:
        state.deliver.assert_not_called()
    if failure == "limited":
        assert response.headers["retry-after"] == "37"


@pytest.mark.parametrize(
    "change,status",
    [
        ({"Cookie": ""}, 401),
        ({"Cookie": "", "Authorization": "Bearer mcp-token"}, 401),
        ({"X-CSRF-Token": ""}, 403),
        ({"X-CSRF-Token": "x" * 43}, 403),
        ({"Origin": "https://untrusted.example"}, 403),
        ({"Origin": ""}, 403),
    ],
)
def test_personalized_delivery_requires_browser_session_origin_and_csrf(
    monkeypatch, change, status
):
    state = make_client(monkeypatch, private=True)
    response = state.client.post(
        state.path, headers={**state.headers, **change}, json={"surface": "feed"}
    )
    assert response.status_code == status
    state.deliver.assert_not_called()
    state.limiter.assert_not_called()
    state.session.commit.assert_not_called()


def test_session_redis_outage_prevents_personalized_delivery(monkeypatch):
    state = make_client(monkeypatch, private=True)
    state.store.get.side_effect = RedisError("private session data")
    response = state.client.post(state.path, headers=state.headers, json={"surface": "feed"})
    assert response.status_code == 503
    assert "private" not in response.text
    state.deliver.assert_not_called()
    state.limiter.assert_not_called()


def test_public_response_has_only_delivery_fields_and_no_partner_private_metadata(monkeypatch):
    state = make_client(monkeypatch)
    output = {
        "product_id": ARTICLE_ID,
        "asset_id": str(uuid.uuid4()),
        "campaign_id": None,
        "name": "Verified TypeScript validator",
        "description": "Validates typed inputs.",
        "url": "https://partner.example/validator",
        "partner_name": "Partner",
        "sponsored": False,
        "reason": "Verified TypeScript support.",
        "receipt": "signed-delivery-receipt",
        "frequency_cap": 2,
    }
    state.deliver.return_value = [{**output, "private_account_note": "private", "user_id": USER_ID}]
    response = state.client.post(state.path, json={"surface": "feed"})
    assert response.status_code == 200
    assert response.json() == {"items": [output]}


def test_route_contract_documents_strict_context_and_browser_cookie_security(placement_client):
    state = placement_client
    document = state.app.openapi()
    operation = document["paths"][state.path]["post"]
    schemas = document["components"]["schemas"]
    body_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    query = schemas[body_ref.rsplit("/", 1)[-1]]
    assert query["additionalProperties"] is False
    assert set(query["properties"]) == {"surface", "article_id", "topic_id", "exclude_product_ids"}
    assert query["properties"]["exclude_product_ids"]["maxItems"] == 100
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PlacementsOut"
    )
    assert bool(operation.get("security")) == state.private


def test_query_model_requires_exactly_the_selected_page_context():
    assert PlacementQuery(surface="topic", topic_id=ARTICLE_ID).topic_id == uuid.UUID(ARTICLE_ID)
    assert PlacementQuery(surface="feed").article_id is None
    with pytest.raises(ValidationError):
        PlacementQuery(surface="topic", article_id=ARTICLE_ID)
