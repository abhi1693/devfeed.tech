"""Read-only partner connector previews retain tenant, credential and request bounds."""

import json
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from devfeed_core.models import (
    PartnerConnection,
    PartnerMembership,
    PartnerPipelineJob,
    PartnerProduct,
)
from devfeed_partner_api.dependencies import get_session
from redis.exceptions import RedisError
from sqlalchemy import func, select
from test_partner_portal import portal_client as portal_client
from test_partner_portal_connections import connection_api as connection_api
from test_partner_portal_connections import create

pytestmark = pytest.mark.integration


def product(index=1, **changes):
    return {
        "id": str(index),
        "name": f"Developer tool {index}",
        "url": f"https://tool{index}.example",
        "listing_url": f"https://catalog.example/tools/{index}",
        "tagline": "Developer tools for inspecting and debugging Python applications.",
        **changes,
    }


@pytest.fixture
def upstream(monkeypatch):
    state = SimpleNamespace(calls=[], data={"items": [product()], "nextCursor": None})

    def fetch(url, *args, **kwargs):
        state.calls.append((url, kwargs))
        return SimpleNamespace(body=json.dumps(state.data).encode())

    monkeypatch.setattr("devfeed_core.partner_connectors._fetch", fetch)
    return state


def body(**changes):
    return {"connector": {"base_url": "https://catalog.example"}, **changes}


@pytest.mark.parametrize("endpoint", ["connector-response", "connector-preview"])
def test_draft_preview_does_not_save_connection_products_or_jobs(
    connection_api, database, upstream, endpoint
):
    client, _, _, _, path = connection_api
    response = client.post(path + "/" + endpoint, json=body())
    assert response.status_code == 200, response.text
    if endpoint == "connector-response":
        assert response.json() == {"data": upstream.data, "sampled": False}
    else:
        assert response.json()["products"][0]["name"] == "Developer tool 1"
        assert response.json()["discovered"] == 1
    with database() as session:
        for model in (PartnerConnection, PartnerProduct, PartnerPipelineJob):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert len(upstream.calls) == 1
    _, request = upstream.calls[0]
    assert request["timeout"] == 5
    assert request["max_bytes"] == 2_000_000
    assert time.monotonic() < request["deadline"] <= time.monotonic() + 20


def test_response_preview_can_diagnose_incorrect_response_paths(connection_api, upstream):
    client, _, _, _, path = connection_api
    draft = body(connector={"base_url": "https://catalog.example", "items_paths": ["missing"]})
    raw = client.post(path + "/connector-response", json=draft)
    assert raw.status_code == 200 and raw.json()["data"] == upstream.data
    mapped = client.post(path + "/connector-preview", json=draft)
    assert mapped.status_code == 422 and "response paths" in mapped.json()["detail"]


def test_mapping_preview_fetches_only_three_details_and_keeps_partial_errors(
    connection_api, upstream, monkeypatch
):
    client, _, _, _, path = connection_api
    calls = []

    def fetch(url, *args, **kwargs):
        calls.append((url, kwargs))
        if "/details/" not in url:
            data = {"items": [product(index) for index in range(1, 6)], "nextCursor": "next"}
        else:
            index = int(url.rsplit("/", 1)[-1])
            data = product(index) if index != 2 else {"id": "another-product"}
        return SimpleNamespace(body=json.dumps(data).encode())

    monkeypatch.setattr("devfeed_core.partner_connectors._fetch", fetch)
    response = client.post(
        path + "/connector-preview",
        json=body(
            connector={"base_url": "https://catalog.example", "detail_path": "/details/{id}"}
        ),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["discovered"] == 5 and result["has_next_page"] is True
    assert [item["external_id"] for item in result["products"]] == ["1", "3"]
    assert len(result["errors"]) == 1
    assert len(calls) == 4 and all(request["timeout"] == 5 for _, request in calls)
    assert len({request["deadline"] for _, request in calls}) == 1


def test_preview_releases_database_transaction_before_upstream_io(
    connection_api, database, monkeypatch
):
    client, _, _, _, path = connection_api
    sessions = []

    def request_session():
        with database() as session:
            sessions.append(session)
            yield session

    def fetch(url, *args, **kwargs):
        assert sessions and not sessions[-1].in_transaction()
        return SimpleNamespace(body=b'{"items": [], "nextCursor": null}')

    client.app.dependency_overrides[get_session] = request_session
    monkeypatch.setattr("devfeed_core.partner_connectors._fetch", fetch)
    assert client.post(path + "/connector-preview", json=body()).status_code == 200


@pytest.mark.parametrize("endpoint", ["connector-response", "connector-preview"])
def test_existing_preview_reuses_bound_credential_without_changing_saved_state(
    connection_api, database, upstream, endpoint
):
    client, _, _, _, path = connection_api
    config = {
        "base_url": "https://catalog.example",
        "auth": {"mode": "bearer", "source": "connection"},
    }
    saved = create(connection_api, connector=config, credential="private-preview-token")
    with database() as session:
        row = session.get(PartnerConnection, saved["provider"])
        original = (row.connector, row.encrypted_credential, row.revision, row.sync_revision)
    upstream.data = {
        "items": [product(tagline="Developer tool private-preview-token for Python.")],
        "nextCursor": None,
    }
    response = client.post(
        f"{path}/{saved['provider']}/{endpoint}",
        json=body(connector=config, expected_revision=saved["revision"]),
    )
    assert response.status_code == 200, response.text
    assert "private-preview-token" not in response.text
    assert upstream.calls[0][1]["origin_headers"] == {
        "Authorization": "Bearer private-preview-token"
    }
    with database() as session:
        row = session.get(PartnerConnection, saved["provider"])
        assert (
            row.connector,
            row.encrypted_credential,
            row.revision,
            row.sync_revision,
        ) == original
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == 0


@pytest.mark.parametrize("changed", ["origin", "mode", "header"])
def test_existing_secret_cannot_move_to_a_different_origin_or_auth_scheme(
    connection_api, upstream, changed
):
    client, _, _, _, path = connection_api
    config = {
        "base_url": "https://catalog.example",
        "auth": {"mode": "api_key", "source": "connection", "header": "X-API-Key"},
    }
    saved = create(connection_api, connector=config, credential="stored-original-token")
    if changed == "origin":
        config["base_url"] = "https://another.example"
    elif changed == "mode":
        config["auth"]["mode"] = "bearer"
    else:
        config["auth"]["header"] = "X-Replacement-Key"
    draft = body(connector=config, expected_revision=saved["revision"])
    url = f"{path}/{saved['provider']}/connector-response"
    rejected = client.post(url, json=draft)
    assert rejected.status_code == 422 and not upstream.calls
    replacement = client.post(url, json={**draft, "credential": "explicit-replacement-token"})
    assert replacement.status_code == 200, replacement.text
    assert len(upstream.calls) == 1
    headers = json.dumps(upstream.calls[0][1]["origin_headers"])
    assert "stored-original-token" not in headers
    assert "explicit-replacement-token" in headers


def test_credential_echoes_and_sensitive_response_fields_are_redacted_and_sampled(
    connection_api, upstream
):
    client, _, _, _, path = connection_api
    secret = "private/Token+?=A"
    encoded = quote(secret, safe="")
    upstream.data = {
        "results": [{"echo": secret + " " + encoded, "content": "x" * 2000}] * 8,
        "accessToken": "upstream-secret-value",
        secret: {"Authorization": "private"},
    }
    response = client.post(
        path + "/connector-response",
        json=body(
            connector={
                "base_url": "https://catalog.example",
                "auth": {"mode": "bearer", "source": "connection"},
            },
            credential=secret,
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["sampled"] is True
    assert len(response.json()["data"]["results"]) == 3
    for sensitive in (secret, encoded, "upstream-secret-value"):
        assert sensitive not in response.text


@pytest.mark.parametrize("endpoint", ["connector-response", "connector-preview"])
def test_managed_connection_previews_never_resolve_administrator_credentials(
    connection_api, database, upstream, monkeypatch, endpoint
):
    client, _, account, _, path = connection_api
    monkeypatch.setenv("DEVFEED_PARTNER_SECRET_ADMIN_ONLY", "private-administrator-token")
    with database.begin() as session:
        session.add(
            PartnerConnection(
                provider="admin-managed",
                name="Managed import",
                account_id=account,
                connector={
                    "base_url": "https://catalog.example",
                    "auth": {"mode": "bearer", "secret_ref": "DEVFEED_PARTNER_SECRET_ADMIN_ONLY"},
                },
            )
        )
    response = client.post(f"{path}/admin-managed/{endpoint}", json=body(expected_revision=1))
    assert response.status_code == 403 and not upstream.calls
    assert "private-administrator-token" not in response.text


def test_live_membership_account_and_connection_scope_precede_upstream_requests(
    connection_api, database, upstream
):
    client, identity, account, other, path = connection_api
    saved = create(connection_api)
    with database.begin() as session:
        session.add(PartnerConnection(provider="foreign", name="Foreign", account_id=other))
    for endpoint in ("connector-response", "connector-preview"):
        assert (
            client.post(
                f"/v1/partner/accounts/{other}/connections/{endpoint}", json=body()
            ).status_code
            == 404
        )
        assert (
            client.post(f"{path}/foreign/{endpoint}", json=body(expected_revision=1)).status_code
            == 404
        )
    with database.begin() as session:
        session.delete(session.get(PartnerMembership, (account, identity.issuer, identity.subject)))
    for endpoint in ("connector-response", "connector-preview"):
        assert client.post(path + "/" + endpoint, json=body()).status_code == 404
        assert (
            client.post(
                f"{path}/{saved['provider']}/{endpoint}",
                json=body(expected_revision=saved["revision"]),
            ).status_code
            == 404
        )
    assert not upstream.calls


@pytest.mark.parametrize("endpoint", ["connector-response", "connector-preview"])
def test_archived_and_stale_connection_previews_do_not_fetch(
    connection_api, database, upstream, endpoint
):
    from devfeed_core.models import utcnow

    client, _, _, _, path = connection_api
    saved = create(connection_api)
    url = f"{path}/{saved['provider']}/{endpoint}"
    assert client.post(url, json=body(expected_revision=saved["revision"] + 1)).status_code == 409
    with database.begin() as session:
        session.get(PartnerConnection, saved["provider"]).archived_at = utcnow()
    assert client.post(url, json=body(expected_revision=saved["revision"])).status_code == 404
    assert not upstream.calls


@pytest.mark.parametrize(
    "config",
    [
        {"base_url": "http://catalog.example"},
        {"base_url": "https://127.0.0.1"},
        {"base_url": "https://169.254.169.254"},
        {"base_url": "https://catalog.example", "list_path": "/products?token=secret"},
        {"base_url": "https://catalog.example", "timeout_seconds": 16},
        {"base_url": "https://catalog.example", "requests_per_minute": 61},
        {"base_url": "https://catalog.example", "max_response_bytes": 2_000_001},
        {
            "base_url": "https://catalog.example",
            "auth": {"mode": "bearer", "secret_ref": "DEVFEED_PARTNER_SECRET_ADMIN_ONLY"},
        },
    ],
)
def test_preview_cannot_relax_partner_request_limits_or_read_server_secrets(
    connection_api, upstream, config
):
    client, _, _, _, path = connection_api
    response = client.post(path + "/connector-response", json=body(connector=config))
    assert response.status_code == 422 and not upstream.calls


def test_previews_share_caller_budget_across_response_and_mapping_checks(connection_api, upstream):
    client, _, _, _, path = connection_api
    for index in range(6):
        endpoint = "connector-response" if index % 2 else "connector-preview"
        assert client.post(path + "/" + endpoint, json=body()).status_code == 200
    limited = client.post(path + "/connector-response", json=body())
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert len(upstream.calls) == 6


def test_account_preview_budget_is_shared_across_account_members(
    connection_api, database, upstream
):
    client, identity, account, _, path = connection_api
    with database.begin() as session:
        session.add(
            PartnerMembership(account_id=account, issuer=identity.issuer, subject="third-member")
        )
    for subject in ("alice", "bob"):
        identity.subject = subject
        for _ in range(6):
            assert client.post(path + "/connector-response", json=body()).status_code == 200
    identity.subject = "third-member"
    assert client.post(path + "/connector-response", json=body()).status_code == 429
    assert len(upstream.calls) == 12


@pytest.mark.parametrize("credential", [None, "", "has space", "line\nbreak", "x" * 4097])
def test_missing_or_invalid_preview_credential_cannot_reach_upstream(
    connection_api, upstream, credential
):
    client, _, _, _, path = connection_api
    response = client.post(
        path + "/connector-response",
        json=body(
            connector={
                "base_url": "https://catalog.example",
                "auth": {"mode": "bearer", "source": "connection"},
            },
            credential=credential,
        ),
    )
    assert response.status_code == 422 and not upstream.calls


def test_preview_budget_failure_is_closed_and_has_retry_diagnostics(
    connection_api, upstream, monkeypatch
):
    client, _, _, _, path = connection_api
    budgets = []

    def consume(redis, limits):
        budgets.extend(limits)
        return 42

    monkeypatch.setattr("devfeed_partner_api.connections.consume_rate_limits", consume)
    limited = client.post(path + "/connector-preview", json=body())
    assert limited.status_code == 429 and limited.headers["retry-after"] == "42"
    assert {limit.limit for limit in budgets} == {6, 12}
    assert all(limit.window == 60 for limit in budgets)

    def unavailable(*args):
        raise RedisError("private-redis-connection-details")

    monkeypatch.setattr("devfeed_partner_api.connections.consume_rate_limits", unavailable)
    failure = client.post(path + "/connector-response", json=body())
    assert failure.status_code == 503 and "private" not in failure.text
    assert not upstream.calls


@pytest.mark.parametrize("endpoint", ["connector-response", "connector-preview"])
def test_upstream_failures_return_safe_errors(connection_api, monkeypatch, endpoint):
    client, _, _, _, path = connection_api

    def unavailable(*args, **kwargs):
        raise ValueError("upstream contained private-token and internal-address")

    monkeypatch.setattr("devfeed_core.partner_connectors._fetch", unavailable)
    response = client.post(path + "/" + endpoint, json=body())
    assert response.status_code == 422 and "private-token" not in response.text
    assert "internal-address" not in response.text
