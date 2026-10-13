"""Partners manage only their own imports; credentials and jobs remain private."""

import json

import pytest
from cryptography.fernet import Fernet
from devfeed_core.config import get_settings
from devfeed_core.models import (
    PartnerConnection,
    PartnerMembership,
    PartnerPipelineJob,
)
from devfeed_core.partner_credentials import connection_credential
from sqlalchemy import select
from test_partner_portal import portal_client as portal_client
from test_partner_portal import seed

pytestmark = pytest.mark.integration


@pytest.fixture
def connection_api(portal_client, database, monkeypatch):
    monkeypatch.setenv("DEVFEED_PARTNER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    client, identity = portal_client
    account, other, *_ = seed(database)
    return client, identity, account, other, f"/v1/partner/accounts/{account}/connections"


def payload(**changes):
    return {
        "name": "Developer catalog",
        "connector": {"base_url": "https://catalog.example", "pagination": {"mode": "none"}},
        "enabled": False,
        **changes,
    }


def create(connection_api, **changes):
    client, _, _, _, path = connection_api
    response = client.post(path, json=payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def test_multiple_accounts_can_use_same_api_without_claiming_provider(connection_api, database):
    client, identity, account, other, path = connection_api
    first = create(connection_api)
    second = create(connection_api)
    assert first["provider"] != second["provider"]
    assert first["account_id"] == second["account_id"] == str(account)
    with database.begin() as session:
        session.add(
            PartnerMembership(account_id=other, issuer=identity.issuer, subject=identity.subject)
        )
    third = client.post(f"/v1/partner/accounts/{other}/connections", json=payload()).json()
    assert third["provider"] not in {first["provider"], second["provider"]}
    assert third["account_id"] == str(other)
    assert client.post(path, json=payload(provider="nick-launches")).status_code == 422
    assert client.post(path, json=payload(account_id=str(other))).status_code == 422


def test_list_detail_pagination_and_jobs_are_account_scoped(connection_api, database):
    client, _, account, other, path = connection_api
    alpha = create(connection_api, name="Alpha")
    beta = create(connection_api, name="Beta")
    with database.begin() as session:
        session.add(PartnerConnection(provider="foreign", name="Foreign", account_id=other))
        session.add(
            PartnerPipelineJob(
                provider=alpha["provider"],
                operation="sync",
                status="failed",
                payload={"secret": "private-server-payload"},
                error="token=private-server-token",
            )
        )
        session.add(PartnerPipelineJob(provider="foreign", operation="sync"))
    page = client.get(path + "?limit=1").json()
    assert page["total"] == 2 and page["items"][0]["provider"] == alpha["provider"]
    assert page["credentials_available"] is True
    assert client.get(path + "?offset=1").json()["items"][0]["provider"] == beta["provider"]
    assert client.get(path + "?q=beta").json()["total"] == 1
    assert client.get(path + "/foreign").status_code == 404
    assert client.get(path + "/foreign/jobs").status_code == 404
    assert client.get(f"/v1/partner/accounts/{other}/connections").status_code == 404
    jobs = client.get(path + "/" + alpha["provider"] + "/jobs").json()
    assert jobs["total"] == 1
    assert set(jobs["items"][0]) == {
        "id",
        "operation",
        "status",
        "attempts",
        "created_at",
        "finished_at",
        "error",
    }
    assert "private-server" not in json.dumps(jobs)
    assert "payload" not in json.dumps(jobs)


def test_live_revocation_and_account_pause_block_all_connection_operations(
    connection_api, database
):
    client, identity, account, _, path = connection_api
    connection = create(connection_api)
    url = path + "/" + connection["provider"]
    with database.begin() as session:
        session.delete(session.get(PartnerMembership, (account, identity.issuer, identity.subject)))
    for method, endpoint, body in [
        ("get", path, None),
        ("get", url, None),
        ("get", url + "/jobs", None),
        ("post", path, payload()),
        ("put", url, {"enabled": True, "expected_revision": connection["revision"]}),
        (
            "post",
            url + "/actions",
            {"action": "resume", "expected_revision": connection["revision"]},
        ),
        ("delete", url, {"expected_revision": connection["revision"]}),
    ]:
        response = client.request(method, endpoint, **({"json": body} if body else {}))
        assert response.status_code == 404, response.text


def test_revision_protected_pause_resume_sync_and_disconnect_cancel_jobs(connection_api, database):
    client, _, _, _, path = connection_api
    value = create(connection_api, enabled=True)
    url = path + "/" + value["provider"]
    revision = value["revision"]
    sync = client.post(url + "/actions", json={"action": "sync", "expected_revision": revision})
    assert sync.status_code == 200
    with database() as session:
        assert len(session.scalars(select(PartnerPipelineJob)).all()) == 1
    paused = client.post(
        url + "/actions", json={"action": "pause", "expected_revision": revision}
    ).json()
    assert not paused["enabled"] and paused["revision"] > revision
    assert client.put(url, json={"enabled": True, "expected_revision": revision}).status_code == 409
    with database() as session:
        assert not session.scalars(
            select(PartnerPipelineJob).where(PartnerPipelineJob.status.in_(["queued", "running"]))
        ).all()
    assert (
        client.post(
            url + "/actions", json={"action": "sync", "expected_revision": paused["revision"]}
        ).status_code
        == 409
    )
    resumed = client.post(
        url + "/actions", json={"action": "resume", "expected_revision": paused["revision"]}
    ).json()
    assert resumed["enabled"] and resumed["state"] == "syncing"
    disconnected = client.request("DELETE", url, json={"expected_revision": resumed["revision"]})
    assert disconnected.status_code == 200 and not disconnected.json()["enabled"]
    assert disconnected.json()["credential_configured"] is False
    with database() as session:
        assert session.get(PartnerConnection, value["provider"]).encrypted_credential is None
    assert client.get(url).status_code == 404
    assert client.get(url + "/jobs").status_code == 404
    assert client.get(path).json()["total"] == 0
    with database() as session:
        assert session.get(PartnerConnection, value["provider"]).archived_at is not None
        assert not session.scalars(
            select(PartnerPipelineJob).where(PartnerPipelineJob.status.in_(["queued", "running"]))
        ).all()


def authenticated_payload(token="private-import-token"):
    return payload(
        connector={
            "base_url": "https://catalog.example",
            "auth": {"mode": "bearer", "source": "connection"},
        },
        credential=token,
        enabled=True,
    )


def test_write_only_token_keep_replace_remove_and_generation_fencing(connection_api, database):
    client, _, _, _, path = connection_api
    response = client.post(path, json=authenticated_payload())
    assert response.status_code == 201, response.text
    value = response.json()
    assert "private-import-token" not in response.text
    assert "encrypted_credential" not in response.text
    assert value["credential_configured"] is True and value["auth_mode"] == "bearer"
    url = path + "/" + value["provider"]
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        encrypted = row.encrypted_credential
        assert connection_credential(row) == "private-import-token"
        job = session.scalar(select(PartnerPipelineJob))
        assert "private-import-token" not in json.dumps(job.payload)
        assert encrypted not in json.dumps(job.payload)
        sync_revision = row.sync_revision
    renamed = client.put(
        url, json={"name": "Renamed", "enabled": True, "expected_revision": value["revision"]}
    ).json()
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        assert row.encrypted_credential == encrypted and row.sync_revision == sync_revision
    replacement = client.put(
        url,
        json={
            "credential": "replacement-token",
            "enabled": True,
            "expected_revision": renamed["revision"],
        },
    )
    assert replacement.status_code == 200, replacement.text
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        assert connection_credential(row) == "replacement-token"
        assert row.sync_revision > sync_revision
        assert (
            len(
                session.scalars(
                    select(PartnerPipelineJob).where(PartnerPipelineJob.status == "failed")
                ).all()
            )
            == 1
        )
        active = session.scalars(
            select(PartnerPipelineJob).where(PartnerPipelineJob.status == "queued")
        ).all()
        assert len(active) == 1
        assert "replacement-token" not in json.dumps(active[0].payload)
    current = replacement.json()
    cleared = client.put(
        url,
        json={
            "connector": {"base_url": "https://catalog.example"},
            "enabled": False,
            "expected_revision": current["revision"],
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert not cleared.json()["credential_configured"]
    with database() as session:
        assert session.get(PartnerConnection, value["provider"]).encrypted_credential is None


def test_origin_or_auth_changes_require_new_token_and_empty_does_not_erase(
    connection_api, database
):
    client, _, _, _, path = connection_api
    value = client.post(path, json=authenticated_payload()).json()
    url = path + "/" + value["provider"]
    body = {
        "connector": {
            "base_url": "https://different.example",
            "auth": {"mode": "bearer", "source": "connection"},
        },
        "enabled": True,
        "expected_revision": value["revision"],
    }
    assert client.put(url, json=body).status_code == 422
    assert (
        client.put(
            url, json={"credential": "", "enabled": True, "expected_revision": value["revision"]}
        ).status_code
        == 422
    )
    with database() as session:
        assert (
            connection_credential(session.get(PartnerConnection, value["provider"]))
            == "private-import-token"
        )
    changed = client.put(url, json={**body, "credential": "new-origin-token"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["connector"]["base_url"] == "https://different.example"


def test_managed_secret_sources_hide_references_and_cannot_redirect_credentials(
    connection_api, database
):
    client, _, account, _, path = connection_api
    with database.begin() as session:
        session.add(
            PartnerConnection(
                provider="managed",
                name="Managed API",
                account_id=account,
                connector={
                    "base_url": "https://managed.example",
                    "list_path": "/products?token=secret-query",
                    "auth": {
                        "mode": "bearer",
                        "secret_ref": "DEVFEED_PARTNER_SECRET_PRIVATE_OTHER_ACCOUNT",
                    },
                },
                last_error="Bearer private-server-token",
            )
        )
    url = path + "/managed"
    response = client.get(url)
    assert response.status_code == 200
    value = response.json()
    assert value["connector"] is None
    assert value["credentials_managed"] and not value["connector_editable"]
    assert "PRIVATE_OTHER_ACCOUNT" not in response.text
    assert "private-server-token" not in response.text
    assert "secret-query" not in response.text
    assert (
        client.put(
            url,
            json={
                "connector": payload()["connector"],
                "enabled": True,
                "expected_revision": value["revision"],
            },
        ).status_code
        == 403
    )
    assert (
        client.put(
            url,
            json={"credential": "stolen", "enabled": True, "expected_revision": value["revision"]},
        ).status_code
        == 403
    )
    updated = client.put(
        url,
        json={
            "name": "Renamed managed import",
            "enabled": False,
            "expected_revision": value["revision"],
        },
    )
    assert updated.status_code == 200, updated.text


@pytest.mark.parametrize(
    "query", ["limit=101", "limit=0", "offset=-1", "sort=products", "q=" + "x" * 201]
)
def test_bounded_connection_lists_reject_invalid_queries(connection_api, query):
    client, _, _, _, path = connection_api
    assert client.get(path + "?" + query).status_code == 422


def test_connection_limit_and_rate_budget_are_bounded(connection_api, database, monkeypatch):
    client, _, account, _, path = connection_api
    with database.begin() as session:
        session.add_all(
            PartnerConnection(provider=f"source-{index}", account_id=account) for index in range(25)
        )
    assert client.post(path, json=payload()).status_code == 409
    removed = client.request("DELETE", path + "/source-0", json={"expected_revision": 1})
    assert removed.status_code == 200
    assert client.get(path).json()["total"] == 24
    assert client.post(path, json=payload()).status_code == 201
    monkeypatch.setattr("devfeed_partner_api.connections.consume_rate_limits", lambda *args: 45)
    limited = client.post(path, json=payload())
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "45"


def test_missing_key_disables_authenticated_setup_without_blocking_disconnect(
    connection_api, database, monkeypatch
):
    client, _, _, _, path = connection_api
    value = client.post(path, json=authenticated_payload()).json()
    monkeypatch.setattr(
        "devfeed_core.partner_credentials.get_settings",
        lambda: type("Settings", (), {"partner_credential_key": None})(),
    )
    assert client.get(path).json()["credentials_available"] is False
    blocked = client.post(path, json=authenticated_payload())
    assert blocked.status_code == 503
    assert "private-import-token" not in blocked.text
    disconnected = client.request(
        "DELETE", path + "/" + value["provider"], json={"expected_revision": value["revision"]}
    )
    assert disconnected.status_code == 200 and not disconnected.json()["enabled"]


def test_encrypted_credentials_reach_only_matching_worker_origin(
    connection_api, database, monkeypatch
):
    from types import SimpleNamespace

    from devfeed_aggregator.partner_sync_tasks import process_pipeline
    from test_partner_tools_integration import finish_sync

    client, _, _, _, path = connection_api
    body = authenticated_payload()
    body["connector"].update(
        {
            "detail_path": "/products/{id}",
            "listing_url_template": "https://catalog.example/listing/{id}",
        }
    )
    created = client.post(path, json=body)
    assert created.status_code == 201, created.text
    record = {
        "id": "checker",
        "name": "API Checker",
        "url": "https://checker.example",
        "description": "Check OpenAPI compatibility before deploying software.",
        "echo": "private-import-token",
    }
    requests = []

    def fetch(url, *args, **kwargs):
        assert url.startswith("https://catalog.example/")
        assert kwargs["origin_headers"] == {"Authorization": "Bearer private-import-token"}
        # The same-origin check also runs on every redirect before a socket opens.
        kwargs["before_request"](url)
        with pytest.raises(ValueError, match="origin"):
            kwargs["before_request"]("https://foreign.example/products")
        requests.append(url)
        response = record if "/products/checker" in url else {"items": [record], "nextCursor": None}
        return SimpleNamespace(body=json.dumps(response).encode())

    monkeypatch.setattr("devfeed_core.partner_connectors._fetch", fetch)
    with database() as session:
        identifier = session.scalar(select(PartnerPipelineJob.id))
    process_pipeline(str(identifier), factory=database)
    finish_sync(
        database,
        str(identifier),
        page_fetcher=lambda url, _: {"final_url": url, "text": "Official developer product"},
    )
    assert len(requests) == 2
    with database() as session:
        jobs = session.scalars(select(PartnerPipelineJob)).all()
        assert "private-import-token" not in json.dumps([job.payload for job in jobs])
        assert session.get(PartnerPipelineJob, identifier).status == "succeeded"


def test_large_connection_pages_keep_shared_statistics_query_budget(connection_api, database):
    from test_api_query_budgets import profile_request

    client, _, account, other, path = connection_api
    with database.begin() as session:
        session.add_all(
            PartnerConnection(
                provider=f"catalog-{index:03}",
                name=f"Catalog {index:03}",
                account_id=account if index < 105 else other,
            )
            for index in range(150)
        )
    _, first = profile_request(client, path + "?limit=100", 4)
    _, second = profile_request(client, path + "?limit=100&offset=100", 4)
    assert first["total"] == second["total"] == 105
    assert len(first["items"]) == 100 and len(second["items"]) == 5
    assert {row["account_id"] for row in first["items"] + second["items"]} == {str(account)}


def test_archived_sources_cannot_be_reenabled_or_establish_ownership(
    connection_api, admin_client, database
):
    from devfeed_aggregator.partner_sync_tasks import schedule_partner_syncs
    from devfeed_core.partner_catalog import active_provider
    from devfeed_core.partner_promotions import associated_products_query
    from test_partner_connection_pagination import listing, product

    client, _, account, _, path = connection_api
    value = create(connection_api, enabled=True)
    url = path + "/" + value["provider"]
    removed = client.request("DELETE", url, json={"expected_revision": value["revision"]})
    assert removed.status_code == 200
    with database.begin() as session:
        connection = session.get(PartnerConnection, value["provider"])
        # Defense in depth for accidental enablement outside the management API.
        connection.enabled = True
        row = product(session, "archived-product")
        listing(session, connection.provider, row)
        product_id = row.id
    admin_url = "/v1/admin/partner-tools/connections/" + value["provider"]
    assert admin_client.get(admin_url).status_code == 404
    assert admin_client.get(admin_url + "/jobs").status_code == 404
    assert (
        admin_client.put(
            admin_url, json={"enabled": True, "expected_revision": removed.json()["revision"]}
        ).status_code
        == 404
    )
    assert admin_client.post(admin_url, json={"action": "connect"}).status_code == 404
    assert admin_client.get("/v1/admin/partner-tools/connections").json()["total"] == 0
    assert (
        client.post(
            url + "/actions",
            json={"action": "resume", "expected_revision": removed.json()["revision"]},
        ).status_code
        == 404
    )
    schedule_partner_syncs(database)
    with database() as session:
        assert active_provider(session, product_id) is None
        assert not session.execute(associated_products_query(account)).all()
        assert not session.scalars(
            select(PartnerPipelineJob).where(PartnerPipelineJob.status.in_(["queued", "running"]))
        ).all()
