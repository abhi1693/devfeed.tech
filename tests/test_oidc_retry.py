"""Recovery never reuses provider credentials or trusts a callback destination."""

import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_http import oidc_retry
from redis.exceptions import ConnectionError
from test_admin_auth import oidc_app as admin_app  # noqa: F401
from test_partner_portal_auth import oidc_app as partner_app  # noqa: F401
from test_user_auth import begin, complete
from test_user_auth import oidc_app as reader_app  # noqa: F401


def query(response):
    return parse_qs(urlsplit(response.headers["location"]).query)


def retry(state, token, **extra):
    response = state.client.get(
        "/v1/user/auth/login", params={"retry": token, **extra}, follow_redirects=False
    )
    if urlsplit(response.headers["location"]).path == "/authorize":
        state.params = query(response)
    return response


@pytest.mark.parametrize(
    "destination", ["/read-later", "/latest?sort=most_liked", "/extension/login-complete"]
)
def test_cancelled_flow_retries_with_fresh_secrets_and_validated_destination(request, destination):
    state = request.getfixturevalue("reader_app")
    original = begin(state, register=True, return_to=destination)
    from devfeed_user_api.auth import key

    original_flow = json.loads(state.store.get(key("flow", original)))
    failed = complete(state, original, extra={"error": "access_denied", "return_to": "//evil.test"})
    params = query(failed)
    assert params["error"] == ["login_cancelled"]
    assert "retry" in params and "ref" in params
    assert "code=" not in failed.headers["location"]
    assert "return_to" not in params and "registration" not in failed.headers["location"]
    assert failed.headers["cache-control"] == "no-store"
    assert failed.headers["referrer-policy"] == "no-referrer"
    response = retry(state, params["retry"][0], register="true", return_to="/latest")
    assert response.status_code == 302
    fresh_state = query(response)["state"][0]
    fresh_flow = json.loads(state.store.get(key("flow", fresh_state)))
    for name in ("state", "nonce", "verifier", "browser"):
        assert fresh_flow[name] != original_flow[name]
    assert query(response).get("prompt") != ["create"]
    success = complete(state, fresh_state)
    assert success.headers["location"] == "https://user.example" + destination
    assert query(retry(state, params["retry"][0]))["error"] == ["login_expired"]


@pytest.mark.parametrize(
    "failure", ["server_error", "temporarily_unavailable", "unknown-sensitive-text"]
)
def test_only_safe_provider_outcomes_reach_the_browser(request, failure):
    state = request.getfixturevalue("reader_app")
    failed = complete(
        state, extra={"error": failure, "error_description": "private account detail"}
    )
    params = query(failed)
    expected = "login_failed" if failure.startswith("unknown") else "login_unavailable"
    assert params["error"] == [expected]
    assert "private" not in failed.headers["location"]
    assert failure not in failed.headers["location"]


@pytest.mark.parametrize("change", ["browser", "policy", "expire"])
def test_retry_is_bound_and_expires_without_consuming_another_browsers_context(request, change):
    state = request.getfixturevalue("reader_app")
    failed = complete(state, extra={"error": "access_denied"})
    token = query(failed)["retry"][0]
    from devfeed_user_api import auth

    retry_key = auth.key("retry", token)
    original = state.store.get(retry_key)
    if change == "browser":
        # Replace the browser's host-scoped cookie rather than append an unscoped duplicate.
        state.client.cookies.set(
            "__Host-devfeed_user_state",
            secrets.token_urlsafe(32),
            domain=state.client.base_url.host,
            path="/",
        )
    elif change == "policy":
        state.settings.oidc_client_id = "different-client"
    else:
        state.store.delete(retry_key)
    assert query(retry(state, token))["error"] == ["login_expired"]
    if change != "expire":
        assert state.store.get(retry_key) == original
    assert not any(":session:" in key for key in state.store.values)


def test_unbound_replayed_and_superseded_callbacks_preserve_existing_session_and_new_flow(
    request,
):
    state = request.getfixturevalue("reader_app")
    completed = begin(state)
    complete(state, completed)
    session = state.client.cookies.get("__Host-devfeed_user_session")
    old = begin(state, return_to="/read-later")
    current = begin(state)
    browser = state.client.cookies.get("__Host-devfeed_user_state")
    for candidate in (completed, old, secrets.token_urlsafe(32)):
        failed = complete(
            state, candidate, extra={"return_to": "/read-later", "error": "access_denied"}
        )
        assert "retry" not in query(failed)
        assert query(failed)["error"] == ["login_expired"]
        assert state.client.cookies.get("__Host-devfeed_user_session") == session
        assert state.client.cookies.get("__Host-devfeed_user_state") == browser
        assert state.client.get("/v1/user/auth/me").json() is not None
    assert complete(state, current).headers["location"] == "https://user.example/"


def test_retry_start_outage_keeps_destination_without_replaying_registration(request, monkeypatch):
    from devfeed_user_api import auth

    state = request.getfixturevalue("reader_app")
    failed = complete(
        state, begin(state, return_to="/read-later"), extra={"error": "access_denied"}
    )
    original_discovery = auth.oidc.discovery
    from devfeed_http.oidc import OIDCDependencyError

    def unavailable(*args):
        raise OIDCDependencyError()

    monkeypatch.setattr(auth.oidc, "discovery", unavailable)
    again = retry(state, query(failed)["retry"][0])
    assert query(again)["error"] == ["login_unavailable"]
    next_token = query(again)["retry"][0]
    monkeypatch.setattr(auth.oidc, "discovery", original_discovery)
    started = retry(state, next_token)
    assert complete(state, query(started)["state"][0]).headers["location"].endswith("/read-later")


def test_retry_redis_failure_does_not_expose_context_or_revoke_session(request, monkeypatch):
    state = request.getfixturevalue("reader_app")
    complete(state)
    session = state.client.cookies.get("__Host-devfeed_user_session")
    flow = begin(state)
    monkeypatch.setattr(state.store, "get", lambda key: (_ for _ in ()).throw(ConnectionError()))
    failed = complete(state, flow)
    assert query(failed)["error"] == ["login_unavailable"]
    assert "retry" not in query(failed)
    assert state.client.cookies.get("__Host-devfeed_user_session") == session


@pytest.mark.parametrize("service", ["admin", "partner"])
def test_privileged_service_retry_is_fresh_and_does_not_cross_service(request, service):
    state = request.getfixturevalue(service + "_app")
    module = __import__(f"devfeed_{service}_api.auth", fromlist=["auth"])
    started = state.client.get(f"/v1/{service}/auth/login", follow_redirects=False)
    failed = state.client.get(
        f"/v1/{service}/auth/callback",
        params={"state": query(started)["state"][0], "error": "access_denied"},
        follow_redirects=False,
    )
    token = query(failed)["retry"][0]
    assert query(failed)["error"] == ["login_cancelled"]
    browser = state.client.cookies.get(module.oidc.cookie_name(state.settings, "state"))
    with pytest.raises(oidc_retry.ExpiredRetry):
        oidc_retry.consume(
            state.store,
            key=lambda kind, value: f"other:{kind}:{value}",
            token=token,
            browser=browser,
            policy=module.oidc.policy_key(state.settings),
        )
    fresh = state.client.get(
        f"/v1/{service}/auth/login", params={"retry": token}, follow_redirects=False
    )
    assert query(fresh)["state"] != query(started)["state"]
    assert query(fresh)["prompt"] == ["login"]
    assert query(fresh)["max_age"] == ["0"]


def test_retry_revalidates_stored_destination_and_rejects_duplicate_contexts(request):
    from devfeed_user_api import auth

    state = request.getfixturevalue("reader_app")
    failed = complete(state, extra={"error": "access_denied"})
    token = query(failed)["retry"][0]
    retry_key = auth.key("retry", token)
    context = json.loads(state.store.get(retry_key))
    assert set(context) == {"browser", "policy", "destination"}
    assert 0 < state.store.values[retry_key][1] - time.time() <= oidc_retry.TTL
    response = state.client.get(
        "/v1/user/auth/login", params=[("retry", token), ("retry", token)], follow_redirects=False
    )
    assert query(response)["error"] == ["login_expired"]
    assert state.store.get(retry_key) is not None
    context["destination"] = "https://evil.test/"
    state.store.set(retry_key, json.dumps(context), ex=oidc_retry.TTL)
    assert query(retry(state, token))["error"] == ["login_expired"]
    assert state.store.get(retry_key) is None


def test_bound_failure_keeps_retry_if_local_account_storage_is_unavailable(request, monkeypatch):
    from devfeed_user_api import auth
    from sqlalchemy.exc import SQLAlchemyError

    state = request.getfixturevalue("reader_app")
    flow = begin(state, return_to="/read-later")
    original_save = auth.save_user_with_status
    monkeypatch.setattr(
        auth, "save_user_with_status", lambda user: (_ for _ in ()).throw(SQLAlchemyError())
    )
    failed = complete(state, flow)
    assert query(failed)["error"] == ["login_unavailable"]
    monkeypatch.setattr(auth, "save_user_with_status", original_save)
    started = retry(state, query(failed)["retry"][0])
    assert complete(state, query(started)["state"][0]).headers["location"].endswith("/read-later")


def test_expired_browser_cookie_has_actionable_feedback_without_trusted_context(request):
    state = request.getfixturevalue("reader_app")
    flow = begin(state, return_to="/read-later")
    state.client.cookies.clear()
    failed = complete(state, flow)
    assert query(failed)["error"] == ["login_expired"]
    assert "retry" not in query(failed)
