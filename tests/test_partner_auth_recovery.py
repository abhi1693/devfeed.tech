"""Partner session failures stay retryable without granting or losing access."""

import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_partner_api import auth
from redis.exceptions import RedisError
from test_partner_portal_auth import ORIGIN, begin, complete
from test_partner_portal_auth import oidc_app as oidc_app


def session_record(state):
    token = state.client.cookies.get("__Host-devfeed_partner_session")
    key = auth.key("session", token)
    return key, json.loads(state.store.get(key))


@pytest.mark.parametrize("providers", [[], ["github"], ["google"], ["github", "google"]])
def test_auth_config_advertises_only_configured_identity_providers(oidc_app, providers):
    for provider in providers:
        setattr(oidc_app.settings, f"oidc_{provider}_idp_id", f"{provider}-provider")

    response = oidc_app.client.get("/v1/partner/auth/config")

    assert response.status_code == 200
    assert response.json() == {"enabled": True, "providers": providers}
    assert oidc_app.requests == []


def test_disabled_auth_exposes_no_providers_or_session_access(oidc_app):
    oidc_app.settings.oidc_client_id = ""
    oidc_app.settings.oidc_github_idp_id = "github-provider"

    assert oidc_app.client.get("/v1/partner/auth/config").json() == {
        "enabled": False,
        "providers": [],
    }
    for route in ("login", "me"):
        response = oidc_app.client.get(f"/v1/partner/auth/{route}", follow_redirects=False)
        assert response.status_code == 503
        assert response.json()["detail"] == "Partner authentication is not configured"
    assert not oidc_app.store.values and not oidc_app.requests


def test_unconfigured_social_provider_cannot_start_a_login_flow(oidc_app):
    response = oidc_app.client.get(
        "/v1/partner/auth/login", params={"provider": "github"}, follow_redirects=False
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Partner sign-in temporarily unavailable"
    assert not oidc_app.store.values
    assert not oidc_app.client.cookies.get("__Host-devfeed_partner_state")


@pytest.mark.parametrize("stage", ["session", "provider-revocation"])
def test_redis_session_outages_preserve_browser_identity_and_recover(oidc_app, monkeypatch, stage):
    assert complete(oidc_app).headers["location"] == ORIGIN + "/"
    token = oidc_app.client.cookies.get("__Host-devfeed_partner_session")
    session_key, _ = session_record(oidc_app)
    original_get = oidc_app.store.get

    def unavailable(key):
        if stage == "session" or key != session_key:
            raise RedisError("private dependency detail")
        return original_get(key)

    monkeypatch.setattr(oidc_app.store, "get", unavailable)
    response = oidc_app.client.get("/v1/partner/auth/me")
    assert response.status_code == 503
    assert response.json() == {"detail": "Partner sessions temporarily unavailable"}
    assert oidc_app.client.cookies.get("__Host-devfeed_partner_session") == token

    monkeypatch.setattr(oidc_app.store, "get", original_get)
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 200


@pytest.mark.parametrize("invalid", ["malformed-json", "missing-identity", "expired-identity"])
def test_untrusted_or_expired_session_records_never_reach_account_storage(oidc_app, invalid):
    complete(oidc_app)
    session_key, record = session_record(oidc_app)
    if invalid == "malformed-json":
        payload = "not-json"
    elif invalid == "missing-identity":
        record.pop("subject")
        payload = json.dumps(record)
    else:
        record["expires_at"] = int(time.time()) - 1
        payload = json.dumps(record)
    oidc_app.store.set(session_key, payload, ex=3600)

    response = oidc_app.client.get("/v1/partner/auth/me")

    assert response.status_code == 401
    assert response.json() == {"detail": "Partner session expired"}


@pytest.mark.parametrize("valid_csrf", [False, True])
def test_authenticated_mutations_require_matching_csrf_with_a_trusted_origin(oidc_app, valid_csrf):
    # Exercise the service's dependency without unrelated account/database handlers.
    @oidc_app.client.app.post("/test/partner-mutation")
    def mutation(partner: auth.Partner):
        return {"subject": partner.subject}

    complete(oidc_app)
    identity = oidc_app.client.get("/v1/partner/auth/me").json()
    response = oidc_app.client.post(
        "/test/partner-mutation",
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": identity["csrf_token"] if valid_csrf else "x" * 43,
        },
    )

    if valid_csrf:
        assert response.status_code == 200
        assert response.json() == {"subject": "partner-1"}
    else:
        assert response.status_code == 403
        assert response.json() == {"detail": "Invalid request origin or CSRF token"}


def test_partner_retry_expiry_does_not_issue_new_provider_credentials(oidc_app):
    response = oidc_app.client.get(
        "/v1/partner/auth/login", params={"retry": "expired"}, follow_redirects=False
    )

    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["error"] == ["login_expired"]
    assert "retry" not in params
    assert not oidc_app.requests and not oidc_app.store.values


def test_partner_retry_survives_a_second_provider_outage_with_a_fresh_context(
    oidc_app, monkeypatch
):
    failed = complete(oidc_app, begin(oidc_app), extra={"error": "access_denied"})
    first_token = parse_qs(urlsplit(failed.headers["location"]).query)["retry"][0]
    original_discovery = auth.oidc.discovery

    def unavailable(*_):
        raise auth.oidc.OIDCError("private provider response")

    monkeypatch.setattr(auth.oidc, "discovery", unavailable)
    failed_again = oidc_app.client.get(
        "/v1/partner/auth/login", params={"retry": first_token}, follow_redirects=False
    )
    params = parse_qs(urlsplit(failed_again.headers["location"]).query)
    assert params["error"] == ["login_unavailable"]
    replacement = params["retry"][0]
    assert replacement != first_token
    assert oidc_app.store.get(auth.key("retry", first_token)) is None
    assert failed_again.headers["cache-control"] == "no-store"
    assert failed_again.headers["referrer-policy"] == "no-referrer"
    assert "private" not in failed_again.headers["location"]

    monkeypatch.setattr(auth.oidc, "discovery", original_discovery)
    started = oidc_app.client.get(
        "/v1/partner/auth/login", params={"retry": replacement}, follow_redirects=False
    )
    oidc_app.params = parse_qs(urlsplit(started.headers["location"]).query)
    assert oidc_app.params["prompt"] == ["login"]
    assert complete(oidc_app, oidc_app.params["state"][0]).headers["location"] == ORIGIN + "/"


@pytest.mark.parametrize("payload", ["not-json", "{}", '{"csrf_token":null}'])
def test_logout_rejects_unverifiable_csrf_without_discarding_the_session(oidc_app, payload):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_partner_session")
    session_key, _ = session_record(oidc_app)
    oidc_app.store.set(session_key, payload, ex=3600)

    response = oidc_app.client.post(
        "/v1/partner/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": "x" * 43}
    )

    assert response.status_code == (403 if "null" in payload else 401)
    assert oidc_app.store.get(session_key) == payload.encode()
    assert oidc_app.client.cookies.get("__Host-devfeed_partner_session") == token


def test_logout_retries_revocation_after_redis_delete_failure(oidc_app, monkeypatch):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_partner_session")
    session_key, record = session_record(oidc_app)
    original_delete = oidc_app.store.delete

    def unavailable(_):
        raise RedisError("private revocation failure")

    monkeypatch.setattr(oidc_app.store, "delete", unavailable)
    headers = {"Origin": ORIGIN, "X-CSRF-Token": record["csrf_token"]}
    response = oidc_app.client.post("/v1/partner/auth/logout", headers=headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "Could not revoke session; retry sign-out"}
    assert oidc_app.store.get(session_key) is not None
    assert oidc_app.client.cookies.get("__Host-devfeed_partner_session") == token

    monkeypatch.setattr(oidc_app.store, "delete", original_delete)
    assert oidc_app.client.post("/v1/partner/auth/logout", headers=headers).status_code == 204
    assert oidc_app.store.get(session_key) is None
    assert oidc_app.client.cookies.get("__Host-devfeed_partner_session") is None


def test_logout_requires_a_configured_origin_even_for_anonymous_requests(oidc_app):
    oidc_app.settings.partner_base_url = None

    response = oidc_app.client.post("/v1/partner/auth/logout", headers={"Origin": ORIGIN})

    assert response.status_code == 503
    assert response.json() == {"detail": "Partner origin is not configured"}


@pytest.mark.parametrize(
    "profile", [{"name": "  Partner  ", "email": "  p@example.com  "}, {"name": " ", "email": ""}]
)
def test_audit_actor_normalizes_optional_profile_without_copying_session_secrets(oidc_app, profile):
    complete(oidc_app)
    record = oidc_app.client.get("/v1/partner/auth/me").json()
    identity = auth.PartnerIdentity.model_validate({**record, **profile})

    actor = auth.actor(identity)

    assert actor == {
        "subject": "partner-1",
        "issuer": "https://identity.example",
        "organization_id": "org-1",
        **({"name": "Partner", "email": "p@example.com"} if profile["name"].strip() else {}),
    }


def test_identity_expiring_before_cookie_creation_does_not_register_or_start_a_session(
    oidc_app, monkeypatch
):
    original_identity = auth.oidc.identity

    def expires_during_callback(*args, **kwargs):
        identity = original_identity(*args, **kwargs)
        # The verified provider response can finish just before the local deadline.
        identity["expires_at"] = int(time.time())
        return identity

    monkeypatch.setattr(auth.oidc, "identity", expires_during_callback)
    response = complete(oidc_app)

    assert response.status_code == 302
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["login_failed"]
    assert oidc_app.client.cookies.get("__Host-devfeed_partner_session") is None
    assert not any(":session:" in key for key in oidc_app.store.values)
    oidc_app.register_user.assert_not_called()
