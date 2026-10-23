"""Administrators use the same encrypted, connection-bound import credentials."""

import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from devfeed_core.config import get_settings
from devfeed_core.models import PartnerAccount, PartnerConnection, PartnerPipelineJob
from devfeed_core.partner_credentials import connection_credential
from sqlalchemy import select

pytestmark = pytest.mark.integration
ROOT = "/v1/admin/partner-tools"
PATH = ROOT + "/connections"
CONFIG = {"base_url": "https://catalog.example", "auth": {"mode": "bearer"}}


@pytest.fixture
def credential_admin(admin_client, database, monkeypatch):
    monkeypatch.setenv("DEVFEED_PARTNER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with database.begin() as session:
        account = PartnerAccount(name="Import owner", tier="bronze")
        other = PartnerAccount(name="Other owner", tier="bronze")
        session.add_all([account, other])
        session.flush()
        account_ids = (str(account.id), str(other.id))
    return admin_client, *account_ids


def create(api, **changes):
    client, account_id, _ = api
    return client.post(
        PATH,
        json={
            "provider": "private-catalog",
            "name": "Private catalog",
            "account_id": account_id,
            "connector": CONFIG,
            "credential": "first-private-token",
            "enabled": True,
            **changes,
        },
    )


def update(client, value, **changes):
    return client.put(
        PATH + "/" + value["provider"],
        json={"expected_revision": value["revision"], "enabled": value["enabled"], **changes},
    )


def test_admin_tokens_are_write_only_retained_replaced_and_fence_import_generations(
    credential_admin, database, monkeypatch
):
    client, _, _ = credential_admin
    created = create(credential_admin)
    assert created.status_code == 201, created.text
    value = created.json()
    assert value["credential_configured"] is True and value["credentials_available"] is True
    assert "first-private-token" not in created.text and "encrypted_credential" not in created.text
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        ciphertext, generation = row.encrypted_credential, row.sync_revision
        assert connection_credential(row) == "first-private-token"
        job = session.scalar(select(PartnerPipelineJob))
        assert ciphertext not in json.dumps(job.payload)
        assert "first-private-token" not in json.dumps(job.payload)
    renamed = update(client, value, name="Renamed")
    assert renamed.status_code == 200, renamed.text
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        assert row.encrypted_credential == ciphertext and row.sync_revision == generation
    replaced = update(client, renamed.json(), credential="replacement-private-token")
    assert replaced.status_code == 200, replaced.text
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        assert connection_credential(row) == "replacement-private-token"
        assert row.sync_revision > generation
        jobs = session.scalars(select(PartnerPipelineJob)).all()
        assert sorted(job.status for job in jobs) == ["failed", "queued"]
        assert all("replacement-private-token" not in json.dumps(job.payload) for job in jobs)
    monkeypatch.setattr(
        "devfeed_core.partner_credentials.get_settings",
        lambda: SimpleNamespace(partner_credential_key=None),
    )
    retained = update(client, replaced.json(), name="Rename while storage key is unavailable")
    assert retained.status_code == 200, retained.text
    assert retained.json()["credential_configured"] is True
    assert retained.json()["credentials_available"] is False


@pytest.mark.parametrize("changed", ["origin", "mode", "header", "account"])
def test_admin_cannot_rebind_a_saved_token_without_replacement(credential_admin, database, changed):
    client, _, other_id = credential_admin
    value = create(credential_admin).json()
    config = {**CONFIG, "auth": {**CONFIG["auth"]}}
    changes = {"connector": config}
    if changed == "origin":
        config["base_url"] = "https://other.example"
    elif changed == "mode":
        config["auth"]["mode"] = "api_key"
    elif changed == "header":
        config["auth"]["header"] = "X-Other-Key"
    else:
        changes["account_id"] = other_id
    blocked = update(client, value, **changes)
    assert blocked.status_code in {409, 422}, blocked.text
    with database() as session:
        assert connection_credential(session.get(PartnerConnection, value["provider"])) == (
            "first-private-token"
        )
    replaced = update(client, value, **changes, credential="explicit-new-binding-token")
    assert replaced.status_code == 200, replaced.text
    with database() as session:
        assert connection_credential(session.get(PartnerConnection, value["provider"])) == (
            "explicit-new-binding-token"
        )


@pytest.mark.parametrize("route", ["connector-preview", "connector-response"])
def test_admin_preview_reuses_only_matching_revision_and_binding_without_saving(
    credential_admin, database, monkeypatch, route
):
    client, _, _ = credential_admin
    value = create(credential_admin).json()
    calls = []

    def fetch(url, *args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(body=b'{"items": [], "nextCursor": null}')

    monkeypatch.setattr("devfeed_core.partner_connectors._fetch", fetch)
    body = {
        "provider": value["provider"],
        "connector": CONFIG,
        "expected_revision": value["revision"],
    }
    response = client.post(ROOT + "/" + route, json=body)
    assert response.status_code == 200, response.text
    assert calls[0]["origin_headers"] == {"Authorization": "Bearer first-private-token"}
    assert "first-private-token" not in response.text
    stale = client.post(ROOT + "/" + route, json={**body, "expected_revision": 999})
    assert stale.status_code == 409 and len(calls) == 1
    foreign = {**CONFIG, "base_url": "https://foreign.example"}
    blocked = client.post(ROOT + "/" + route, json={**body, "connector": foreign})
    assert blocked.status_code == 422 and len(calls) == 1
    replacement = client.post(
        ROOT + "/" + route,
        json={**body, "connector": foreign, "credential": "preview-only-token"},
    )
    assert replacement.status_code == 200, replacement.text
    assert calls[-1]["origin_headers"] == {"Authorization": "Bearer preview-only-token"}
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        assert row.revision == value["revision"]
        assert connection_credential(row) == "first-private-token"
        assert len(session.scalars(select(PartnerPipelineJob)).all()) == 1


def test_missing_database_credential_cannot_enable_or_preview(credential_admin, database):
    client, _, _ = credential_admin
    value = create(credential_admin, credential=None, enabled=False).json()
    assert value["credential_configured"] is False
    assert update(client, value, enabled=True).status_code == 422
    assert client.post(PATH + "/private-catalog", json={"action": "connect"}).status_code == 422
    for route in ("connector-preview", "connector-response"):
        response = client.post(
            ROOT + "/" + route,
            json={"provider": value["provider"], "connector": CONFIG},
        )
        assert response.status_code == 422
    with database() as session:
        assert not session.get(PartnerConnection, value["provider"]).enabled
        assert session.scalar(select(PartnerPipelineJob)) is None


def test_admin_auth_requires_account_and_can_be_explicitly_removed(credential_admin, database):
    client, _, other = credential_admin
    missing_account = create(credential_admin, account_id=None)
    assert missing_account.status_code == 422
    value = create(credential_admin).json()
    cleared = update(
        client,
        value,
        enabled=False,
        account_id=other,
        connector={"base_url": "https://catalog.example"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["credential_configured"] is False
    with database() as session:
        row = session.get(PartnerConnection, value["provider"])
        assert row.encrypted_credential is None
        assert str(row.account_id) == other
