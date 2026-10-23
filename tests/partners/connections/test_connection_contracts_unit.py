"""HTTP import commands keep credentials write-only and guard current revisions.

Account/resource reads and downstream commands are boundaries here. PostgreSQL
membership filtering, locks, and transaction atomicity remain integration contracts.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from devfeed_admin_api import dependencies as admin_dependencies
from devfeed_admin_api import partner_tools as admin_tools
from devfeed_admin_api.auth import AdminIdentity, require_admin
from devfeed_core.config import get_settings
from devfeed_core.models import PartnerAccount, PartnerConnection, PartnerPipelineJob
from devfeed_core.partner_connections import ConnectionOut
from devfeed_core.partner_connectors import ConnectorConfig
from devfeed_core.partner_credentials import connection_credential, encrypt_credential
from devfeed_partner_api import connections as partner_connections
from devfeed_partner_api import dependencies as partner_dependencies
from devfeed_partner_api.auth import PartnerIdentity, require_partner
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
SECRET = "unit-import-credential"


def connector(mode="none", **changes):
    return ConnectorConfig.model_validate(
        {
            "base_url": "https://catalog.example",
            "pagination": {"mode": "none"},
            "auth": {"mode": mode},
            **changes,
        }
    )


def connection_output(connection, account):
    config = ConnectorConfig.model_validate(connection.connector)
    return ConnectionOut(
        provider=connection.provider,
        name=connection.name,
        account_id=connection.account_id,
        account_tier=account.tier,
        sync_interval_minutes=connection.sync_interval_minutes,
        revision=connection.revision,
        api_url=config.base_url + config.list_path,
        connector=config,
        enabled=connection.enabled,
        state="idle" if connection.enabled else "disconnected",
        last_sync_at=None,
        next_sync_at=None,
        error=connection.last_error,
        products=0,
        qualified=0,
        checking=0,
        needs_attention=0,
        excluded=0,
        ai_enabled=False,
        credential_configured=bool(connection.encrypted_credential),
        credentials_available=get_settings().partner_credential_key is not None,
    )


@pytest.fixture(params=["admin", "partner"])
def api(request, monkeypatch):
    surface = request.param
    module = admin_tools if surface == "admin" else partner_connections
    identity_type = AdminIdentity if surface == "admin" else PartnerIdentity
    identity = identity_type(
        subject="operator",
        issuer="https://identity.example",
        organization_id="org",
        roles=["superuser" if surface == "admin" else "partner"],
        expires_at=4_102_444_800,
        csrf_token="unit-csrf",
    )
    account = PartnerAccount(id=uuid.uuid4(), name="Import owner", tier="gold", status="active")
    connection = PartnerConnection(
        provider="catalog-api",
        account_id=account.id,
        name="Catalog API",
        connector=connector().model_dump(mode="json"),
        enabled=True,
        revision=3,
        sync_revision=2,
        sync_interval_minutes=1440,
        archived_at=None,
        encrypted_credential=None,
        last_error=None,
    )
    session = Mock(spec=Session)
    session.scalar.return_value = connection
    session.get.side_effect = lambda model, _: (
        connection if model is PartnerConnection else account if model is PartnerAccount else None
    )
    access = Mock()
    access.require.return_value = access.lock.return_value = account
    monkeypatch.setattr(partner_connections, "PartnerAccountAccess", Mock(return_value=access))
    monkeypatch.setattr(partner_connections, "get_redis", Mock(return_value=object()))
    rate_limits = Mock(return_value=0)
    monkeypatch.setattr(partner_connections, "consume_rate_limits", rate_limits)
    monkeypatch.setattr(
        get_settings(), "partner_credential_key", SecretStr(Fernet.generate_key().decode())
    )
    monkeypatch.setattr(module, "lock_catalog", Mock())
    monkeypatch.setattr(
        admin_tools, "connection_view", lambda _, row: connection_output(row, account)
    )
    monkeypatch.setattr(
        partner_connections,
        "connection_views",
        lambda _, rows: [connection_output(row, account) for row in rows],
    )

    def configure(_, row, enabled, **options):
        # A downstream command response, without simulating SQL or queued jobs.
        row.enabled = enabled
        return []

    command = Mock(side_effect=configure)
    monkeypatch.setattr(module, "configure_connection", command)
    preview = Mock()
    preview.products.return_value = {
        "products": [],
        "errors": [],
        "has_next_page": False,
        "discovered": 0,
    }
    preview.response.return_value = {"data": {"items": []}, "sampled": True}
    preview_factory = Mock(return_value=preview)
    monkeypatch.setattr(module, "ConnectorPreviewService", preview_factory)
    app = FastAPI()
    app.include_router(module.router)
    auth_dependency = require_admin if surface == "admin" else require_partner
    db_dependency = (
        admin_dependencies.get_session if surface == "admin" else partner_dependencies.get_session
    )
    app.dependency_overrides[auth_dependency] = lambda: identity
    app.dependency_overrides[db_dependency] = lambda: session
    base = (
        "/v1/admin/partner-tools"
        if surface == "admin"
        else f"/v1/partner/accounts/{account.id}/connections"
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            surface=surface,
            module=module,
            identity=identity,
            account=account,
            connection=connection,
            session=session,
            access=access,
            limits=rate_limits,
            command=command,
            preview=preview,
            factory=preview_factory,
            app=app,
            auth_dependency=auth_dependency,
            client=client,
            base=base,
        )


def preview_request(api, config, *, existing=False, credential=None, response=False):
    body = {"connector": config.model_dump(mode="json")}
    if credential is not None:
        body["credential"] = credential
    if api.surface == "admin":
        body["provider"] = api.connection.provider
    if existing:
        body["expected_revision"] = api.connection.revision
    suffix = "/connector-response" if response else "/connector-preview"
    path = (
        api.base
        + (f"/{api.connection.provider}" if existing and api.surface == "partner" else "")
        + suffix
    )
    return path, body


@pytest.mark.parametrize("mode", ["none", "bearer", "api_key"])
@pytest.mark.parametrize("existing", [False, True])
def test_preview_forwards_only_authorized_connector_and_resolved_credential(api, mode, existing):
    config = connector(mode)
    api.connection.connector = config.model_dump(mode="json")
    if mode != "none" and existing:
        api.connection.encrypted_credential = encrypt_credential(api.connection, config, SECRET)
    path, body = preview_request(
        api,
        config,
        existing=existing,
        credential=SECRET if mode != "none" and not existing else None,
    )

    response = api.client.post(path, json=body)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "products": [],
        "errors": [],
        "has_next_page": False,
        "discovered": 0,
    }
    assert api.factory.call_args.kwargs == {"credential": SECRET if mode != "none" else None}
    assert api.factory.call_args.args[1].model_dump(mode="json") == config.model_dump(mode="json")
    api.session.rollback.assert_called_once()
    api.session.commit.assert_not_called()
    api.preview.products.assert_called_once_with()
    assert SECRET not in response.text and "encrypted_credential" not in response.text
    if api.surface == "partner":
        api.access.require.assert_called_once_with(api.account.id)


@pytest.mark.parametrize("existing", [False, True])
def test_raw_preview_uses_the_response_command_without_persisting_anything(api, existing):
    path, body = preview_request(api, connector(), existing=existing, response=True)

    response = api.client.post(path, json=body)

    assert response.status_code == 200 and response.json() == {
        "data": {"items": []},
        "sampled": True,
    }
    api.preview.response.assert_called_once_with()
    api.preview.products.assert_not_called()
    api.session.add.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "problem",
    ["credential-without-auth", "missing-credential", "invalid-credential", "changed-origin"],
)
def test_preview_refuses_ambiguous_missing_or_wrongly_bound_credentials(api, problem):
    config = connector("none" if problem == "credential-without-auth" else "bearer")
    supplied = (
        SECRET
        if problem == "credential-without-auth"
        else "contains a space"
        if problem == "invalid-credential"
        else None
    )
    existing = problem == "changed-origin"
    if existing:
        original = connector("bearer")
        api.connection.connector = original.model_dump(mode="json")
        api.connection.encrypted_credential = encrypt_credential(api.connection, original, SECRET)
        config = connector("bearer", base_url="https://replacement.example")
    path, body = preview_request(api, config, existing=existing, credential=supplied)

    response = api.client.post(path, json=body)

    assert response.status_code == 422
    assert SECRET not in response.text and "contains a space" not in response.text
    api.factory.assert_not_called()
    api.session.commit.assert_not_called()


def test_saved_preview_refuses_a_stale_revision_before_upstream_io(api):
    path, body = preview_request(api, connector(), existing=True)
    body["expected_revision"] -= 1

    response = api.client.post(path, json=body)

    assert response.status_code == 409 and "changed" in response.json()["detail"]
    api.factory.assert_not_called()
    api.session.commit.assert_not_called()


def test_authentication_denial_precedes_preview_or_repository_access(api):
    def deny():
        raise HTTPException(401, "Sign in required")

    api.app.dependency_overrides[api.auth_dependency] = deny
    path, body = preview_request(api, connector())

    response = api.client.post(path, json=body)

    assert response.status_code == 401
    api.session.scalar.assert_not_called()
    api.factory.assert_not_called()


@pytest.mark.parametrize("action", ["pause", "resume", "sync"])
def test_actions_forward_explicit_enable_and_sync_intent_without_returning_credentials(api, action):
    if api.surface == "admin" and action == "resume":
        action = "connect"
    api.connection.connector = connector("bearer").model_dump(mode="json")
    api.connection.encrypted_credential = encrypt_credential(
        api.connection, connector("bearer"), SECRET
    )
    body = {"action": action}
    if api.surface == "partner":
        body["expected_revision"] = api.connection.revision
    path = api.base + (
        f"/connections/{api.connection.provider}"
        if api.surface == "admin"
        else f"/{api.connection.provider}/actions"
    )

    response = api.client.post(path, json=body)

    assert response.status_code == 200, response.text
    enabled = action != "pause"
    api.command.assert_called_once_with(api.session, api.connection, enabled, sync=enabled)
    assert response.json()["enabled"] is enabled
    assert response.json()["credential_configured"] is True
    assert SECRET not in response.text and api.connection.encrypted_credential not in response.text
    assert api.connection.updated_by == {
        "subject": api.identity.subject,
        "issuer": api.identity.issuer,
        "organization_id": api.identity.organization_id,
    }
    api.session.commit.assert_called_once()


def test_syncing_a_paused_import_conflicts_without_validating_or_dispatching_credentials(api):
    api.connection.enabled = False
    api.connection.connector = connector("bearer").model_dump(mode="json")
    body = {"action": "sync"}
    if api.surface == "partner":
        body["expected_revision"] = api.connection.revision
    path = api.base + (
        f"/connections/{api.connection.provider}"
        if api.surface == "admin"
        else f"/{api.connection.provider}/actions"
    )

    response = api.client.post(path, json=body)

    assert response.status_code == 409 and "before syncing" in response.json()["detail"]
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


def test_pause_remains_available_when_saved_credentials_cannot_be_decrypted(api, monkeypatch):
    api.connection.connector = connector("bearer").model_dump(mode="json")
    api.connection.encrypted_credential = encrypt_credential(
        api.connection, connector("bearer"), SECRET
    )
    saved_ciphertext = api.connection.encrypted_credential
    monkeypatch.setattr(get_settings(), "partner_credential_key", None)
    body = {"action": "pause"}
    if api.surface == "partner":
        body["expected_revision"] = api.connection.revision
    path = api.base + (
        f"/connections/{api.connection.provider}"
        if api.surface == "admin"
        else f"/{api.connection.provider}/actions"
    )

    response = api.client.post(path, json=body)

    assert response.status_code == 200 and response.json()["enabled"] is False
    assert saved_ciphertext not in response.text
    api.command.assert_called_once_with(api.session, api.connection, False, sync=False)


@pytest.mark.parametrize("storage_available", [False, True])
def test_authenticated_sync_reports_missing_storage_or_missing_token_without_dispatch(
    api, monkeypatch, storage_available
):
    api.connection.connector = connector("bearer").model_dump(mode="json")
    if not storage_available:
        monkeypatch.setattr(get_settings(), "partner_credential_key", None)
    body = {"action": "sync"}
    if api.surface == "partner":
        body["expected_revision"] = api.connection.revision
    path = api.base + (
        f"/connections/{api.connection.provider}"
        if api.surface == "admin"
        else f"/{api.connection.provider}/actions"
    )

    response = api.client.post(path, json=body)

    assert response.status_code == (422 if storage_available else 503)
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("retained", [False, True])
def test_admin_disabling_authentication_removes_any_saved_credential(monkeypatch, retained):
    row = PartnerConnection(
        account_id=uuid.uuid4(),
        provider="catalog-api",
        encrypted_credential="prior-ciphertext" if retained else None,
    )

    changed = admin_tools.apply_connection_credential(row, connector(), None, required=False)

    assert changed is retained and row.encrypted_credential is None


@pytest.mark.parametrize("api", ["admin"], indirect=True)
def test_admin_credential_replacement_is_bound_to_the_current_connection(api):
    config = connector("bearer")

    assert admin_tools.apply_connection_credential(
        api.connection, config, SecretStr(SECRET), required=True
    )
    saved = api.connection.encrypted_credential
    assert saved != SECRET and connection_credential(api.connection, config) == SECRET
    assert not admin_tools.apply_connection_credential(
        api.connection, config, None, required=True, verify=True
    )
    assert api.connection.encrypted_credential == saved


@pytest.mark.parametrize("failure", ["denied", "rate-limit", "redis-down"])
@pytest.mark.parametrize("api", ["partner"], indirect=True)
def test_partner_preview_authority_and_capacity_fail_before_upstream_io(api, failure):
    if failure == "denied":
        api.access.require.side_effect = HTTPException(404, "Partner account not found")
    elif failure == "rate-limit":
        api.limits.return_value = 17
    else:
        api.limits.side_effect = RedisConnectionError("not available")
    path, body = preview_request(api, connector())

    response = api.client.post(path, json=body)

    assert response.status_code == {"denied": 404, "rate-limit": 429, "redis-down": 503}[failure]
    if failure == "rate-limit":
        assert response.headers["Retry-After"] == "17"
    api.factory.assert_not_called()
    api.session.commit.assert_not_called()


def update_request(api, **changes):
    path = api.base + (
        f"/connections/{api.connection.provider}"
        if api.surface == "admin"
        else f"/{api.connection.provider}"
    )
    return path, {"expected_revision": api.connection.revision, "enabled": True, **changes}


@pytest.mark.parametrize("credential_change", ["replace", "remove", "retain"])
def test_settings_forward_definition_change_and_keep_credentials_write_only(
    api, monkeypatch, credential_change
):
    old_config = connector("bearer")
    api.connection.connector = old_config.model_dump(mode="json")
    api.connection.encrypted_credential = encrypt_credential(api.connection, old_config, SECRET)
    config = (
        connector("bearer", base_url="https://replacement.example")
        if credential_change == "replace"
        else connector()
        if credential_change == "remove"
        else old_config
    )

    def update_definition(_, row, config, *, credential_changed):
        # A successful service command publishes the validated definition on its model.
        row.connector = config.model_dump(mode="json")
        return credential_changed

    definition = Mock(side_effect=update_definition)
    monkeypatch.setattr(api.module, "update_connection_definition", definition)
    changes = {"name": "Renamed catalog", "sync_interval_minutes": 720}
    if credential_change != "retain":
        changes["connector"] = config.model_dump(mode="json")
    if credential_change == "replace":
        changes["credential"] = "replacement-import-token"
    path, body = update_request(api, **changes)

    response = api.client.put(path, json=body)

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed catalog"
    assert api.connection.revision == 4  # This handler owns the name revision only.
    if credential_change == "remove":
        assert api.connection.encrypted_credential is None
    else:
        assert connection_credential(api.connection, config) == (
            "replacement-import-token" if credential_change == "replace" else SECRET
        )
    if credential_change != "retain" or api.surface == "partner":
        args, options = definition.call_args
        assert args[:2] == (api.session, api.connection)
        assert args[2].model_dump(mode="json") == config.model_dump(mode="json")
        assert options == {"credential_changed": credential_change != "retain"}
    else:
        definition.assert_not_called()
    api.command.assert_called_once_with(
        api.session,
        api.connection,
        True,
        sync_interval_minutes=720,
        sync=credential_change != "retain",
    )
    api.session.commit.assert_called_once()
    assert SECRET not in response.text and "replacement-import-token" not in response.text


def test_settings_cannot_reuse_a_credential_at_a_different_origin(api, monkeypatch):
    original = connector("bearer")
    api.connection.connector = original.model_dump(mode="json")
    api.connection.encrypted_credential = encrypt_credential(api.connection, original, SECRET)
    definition = Mock()
    monkeypatch.setattr(api.module, "update_connection_definition", definition)
    path, body = update_request(
        api, connector=connector("bearer", base_url="https://other.example").model_dump(mode="json")
    )

    response = api.client.put(path, json=body)

    assert response.status_code == 422
    definition.assert_not_called()
    api.command.assert_not_called()
    api.session.commit.assert_not_called()
    assert connection_credential(api.connection, original) == SECRET


def test_settings_stale_revision_rejects_before_a_definition_or_command_change(api, monkeypatch):
    definition = Mock()
    monkeypatch.setattr(api.module, "update_connection_definition", definition)
    path, body = update_request(api, expected_revision=2, name="Stale name")

    response = api.client.put(path, json=body)

    assert response.status_code == 409 and api.connection.name == "Catalog API"
    definition.assert_not_called()
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("api", ["admin"], indirect=True)
def test_admin_account_reassignment_requires_replacement_of_account_bound_credential(api):
    config = connector("bearer")
    api.connection.connector = config.model_dump(mode="json")
    api.connection.encrypted_credential = encrypt_credential(api.connection, config, SECRET)
    original_account = api.connection.account_id
    path, body = update_request(api, account_id=str(uuid.uuid4()))

    response = api.client.put(path, json=body)

    assert response.status_code == 409 and "account-bound" in response.json()["detail"]
    assert api.connection.account_id == original_account
    assert connection_credential(api.connection, config) == SECRET
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("api", ["partner"], indirect=True)
@pytest.mark.parametrize("credential_present", [False, True])
def test_disconnect_archives_a_paused_connection_and_erases_its_saved_token(
    api, credential_present
):
    api.connection.enabled = False
    api.connection.connector = connector("bearer").model_dump(mode="json")
    if credential_present:
        api.connection.encrypted_credential = encrypt_credential(
            api.connection, connector("bearer"), SECRET
        )

    response = api.client.request(
        "DELETE",
        f"{api.base}/{api.connection.provider}",
        json={"expected_revision": api.connection.revision},
    )

    assert response.status_code == 200, response.text
    assert response.json()["credential_configured"] is False
    assert api.connection.archived_at is not None
    assert api.connection.encrypted_credential is None
    assert api.connection.revision == 3 + credential_present
    assert api.connection.sync_revision == 2 + credential_present
    api.command.assert_called_once_with(api.session, api.connection, False)
    api.session.commit.assert_called_once()


@pytest.mark.parametrize("api", ["partner"], indirect=True)
@pytest.mark.parametrize("interval,allowed", [(60, False), (720, True), (1440, True)])
def test_partner_interval_entitlement_is_checked_before_dispatch(
    api, monkeypatch, interval, allowed
):
    api.account.tier = "gold"
    definition = Mock(return_value=False)
    monkeypatch.setattr(api.module, "update_connection_definition", definition)
    path, body = update_request(api, sync_interval_minutes=interval)

    response = api.client.put(path, json=body)

    assert response.status_code == (200 if allowed else 422), response.text
    if allowed:
        assert api.command.call_args.kwargs["sync_interval_minutes"] == interval
        api.session.commit.assert_called_once()
    else:
        assert "12, 24 hours" in response.json()["detail"]
        definition.assert_not_called()
        api.command.assert_not_called()
        api.session.commit.assert_not_called()


def creation_request(api, mode="none", **changes):
    body = {
        "name": "A new import",
        "connector": connector(mode).model_dump(mode="json"),
        "sync_interval_minutes": 1440,
        **changes,
    }
    if api.surface == "admin":
        body.update(provider="new-catalog", account_id=str(api.account.id))
    return api.base + ("/connections" if api.surface == "admin" else ""), body


@pytest.mark.parametrize("mode", ["none", "bearer", "api_key"])
def test_create_passes_an_account_bound_model_to_the_import_command(api, monkeypatch, mode):
    # Supplied existence/count reads, without evaluating the SQL count or account filter.
    api.session.get.side_effect = lambda model, _: api.account if model is PartnerAccount else None
    api.session.scalar.return_value = 0
    projection = connection_output(api.connection, api.account)

    def projected(row):
        # The read projection supplies DB-generated defaults; persistence is not simulated.
        return projection.model_copy(
            update={
                "provider": row.provider,
                "name": row.name,
                "account_id": row.account_id,
                "connector": ConnectorConfig.model_validate(row.connector),
                "enabled": row.enabled,
                "credential_configured": bool(row.encrypted_credential),
                "revision": 1,
            }
        )

    monkeypatch.setattr(admin_tools, "connection_view", lambda _, row: projected(row))
    monkeypatch.setattr(
        partner_connections, "connection_views", lambda _, rows: [projected(row) for row in rows]
    )
    path, body = creation_request(api, mode, **({"credential": SECRET} if mode != "none" else {}))

    response = api.client.post(path, json=body)

    assert response.status_code == 201, response.text
    created = api.session.add.call_args.args[0]
    assert isinstance(created, PartnerConnection)
    assert created.account_id == api.account.id and created.name == "A new import"
    assert ConnectorConfig.model_validate(created.connector).auth.mode == mode
    assert connection_credential(created) == (SECRET if mode != "none" else None)
    assert created.encrypted_credential != SECRET
    assert created.updated_by == {
        "subject": api.identity.subject,
        "issuer": api.identity.issuer,
        "organization_id": api.identity.organization_id,
    }
    api.command.assert_called_once_with(api.session, created, True)
    api.session.flush.assert_called_once()
    api.session.commit.assert_called_once()
    assert response.json()["credential_configured"] is (mode != "none")
    assert SECRET not in response.text and "encrypted_credential" not in response.text
    if api.surface == "partner":
        assert created.provider.startswith(f"account-{api.account.id.hex}-")
        assert any(
            budget.limit == 10 and budget.window == 3600 for budget in api.limits.call_args.args[1]
        )
    else:
        assert created.provider == "new-catalog"


@pytest.mark.parametrize("api", ["admin"], indirect=True)
@pytest.mark.parametrize("missing", ["definition", "name", "account"])
def test_custom_admin_creation_requires_definition_name_and_existing_account(api, missing):
    api.session.get.side_effect = lambda model, _: (
        api.account if model is PartnerAccount and missing != "account" else None
    )
    path, body = creation_request(api)
    if missing != "account":
        body.pop("connector" if missing == "definition" else "name")

    response = api.client.post(path, json=body)

    assert response.status_code == 422
    assert {"definition": "definition", "name": "name", "account": "account"}[
        missing
    ] in response.json()["detail"]
    api.session.add.assert_not_called()
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("api", ["partner"], indirect=True)
@pytest.mark.parametrize("counts", [[25], [24, 25]])
def test_partner_creation_refuses_a_full_catalog_at_each_authoritative_count_read(api, counts):
    api.session.scalar.side_effect = counts
    path, body = creation_request(api)

    response = api.client.post(path, json=body)

    assert response.status_code == 409 and "25 import connection limit" in response.json()["detail"]
    api.session.add.assert_not_called()
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("api", ["partner"], indirect=True)
@pytest.mark.parametrize("failure", ["denied", "rate-limit", "redis-down"])
def test_partner_write_authority_and_capacity_fail_before_changing_settings(api, failure):
    if failure == "denied":
        api.access.require.side_effect = HTTPException(404, "Partner account not found")
    elif failure == "rate-limit":
        api.limits.return_value = 31
    else:
        api.limits.side_effect = RedisConnectionError("not available")
    path, body = update_request(api, name="Should not change")

    response = api.client.put(path, json=body)

    assert response.status_code == {"denied": 404, "rate-limit": 429, "redis-down": 503}[failure]
    assert api.connection.name == "Catalog API"
    if failure == "rate-limit":
        assert response.headers["Retry-After"] == "31"
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("api", ["partner"], indirect=True)
@pytest.mark.parametrize("path_field", ["list_path", "detail_path"])
def test_partner_parameters_cannot_be_hidden_in_connector_paths(api, path_field):
    endpoint = "/products/{id}" if path_field == "detail_path" else "/products"
    path, body = preview_request(
        api, connector(**{path_field: endpoint + "?token=embedded-secret"})
    )

    response = api.client.post(path, json=body)

    assert response.status_code == 422
    assert "query parameter fields" in response.text
    api.factory.assert_not_called()
    api.session.scalar.assert_not_called()


@pytest.mark.parametrize("api", ["partner"], indirect=True)
@pytest.mark.parametrize("upstream_error", [None, "Authorization: unit-import-credential rejected"])
def test_partner_connection_get_sanitizes_upstream_errors_and_parameterized_api_urls(
    api, upstream_error
):
    api.connection.connector = connector(list_path="/products?region=private").model_dump(
        mode="json"
    )
    api.connection.last_error = upstream_error

    response = api.client.get(f"{api.base}/{api.connection.provider}")

    assert response.status_code == 200
    body = response.json()
    assert body["api_url"] == "https://catalog.example/products"
    assert body["auth_mode"] == "none" and body["credential_header"] == "X-API-Key"
    assert body["error"] == (
        "The latest import failed. Review the connection settings and try again."
        if upstream_error
        else None
    )
    assert SECRET not in response.text and "region=private" not in body["api_url"]
    api.access.require.assert_called_once_with(api.account.id)


def test_missing_connection_get_is_not_found_without_exposing_a_service_projection(
    api, monkeypatch
):
    api.session.get.side_effect = None
    api.session.get.return_value = None
    api.session.scalar.return_value = None
    project = Mock()
    monkeypatch.setattr(
        api.module,
        "connection_view" if api.surface == "admin" else "partner_connection_views",
        project,
    )
    path = api.base + (
        f"/connections/{api.connection.provider}"
        if api.surface == "admin"
        else f"/{api.connection.provider}"
    )

    response = api.client.get(path)

    assert response.status_code == 404
    project.assert_not_called()


@pytest.mark.parametrize("api", ["admin"], indirect=True)
@pytest.mark.parametrize("storage_available", [False, True])
def test_admin_presets_report_credential_storage_availability_without_a_database_read(
    api, monkeypatch, storage_available
):
    if not storage_available:
        monkeypatch.setattr(get_settings(), "partner_credential_key", None)

    response = api.client.get(f"{api.base}/providers")

    assert response.status_code == 200
    assert response.json() and all(
        row["credentials_available"] is storage_available for row in response.json()
    )
    api.session.scalar.assert_not_called()
    api.session.get.assert_not_called()


@pytest.mark.parametrize("api", ["partner"], indirect=True)
@pytest.mark.parametrize("search", [None, "failed"])
def test_partner_job_history_projects_status_and_redacts_payloads_and_diagnostics(
    api, monkeypatch, search
):
    rows = [
        PartnerPipelineJob(
            id=uuid.uuid4(),
            provider=api.connection.provider,
            operation="sync",
            status=status,
            attempts=1,
            created_at=NOW - timedelta(minutes=2),
            available_at=NOW - timedelta(minutes=2),
            finished_at=NOW,
            lease_token=None,
            lease_until=None,
            payload={"upstream_token": SECRET, "discovery_complete": status == "succeeded"},
            error=f"Upstream rejected {SECRET}" if status == "failed" else None,
        )
        for status in (["failed"] if search else ["succeeded", "failed"])
    ]
    page_read = Mock(return_value={"items": rows, "total": len(rows), "limit": 25, "offset": 0})
    monkeypatch.setattr(api.module, "paginate", page_read)

    response = api.client.get(
        f"{api.base}/{api.connection.provider}/jobs", params={"q": search} if search else {}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(rows) and [row["status"] for row in body["items"]] == [
        row.status for row in rows
    ]
    for row in body["items"]:
        assert set(row) == {
            "id",
            "operation",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "error",
        }
        assert row["error"] == (
            "Import did not complete. Review the connection settings and retry."
            if row["status"] == "failed"
            else None
        )
    assert SECRET not in response.text and "upstream_token" not in response.text
    assert page_read.call_args.args[2].q == (search or "")
    api.access.require.assert_called_once_with(api.account.id)


@pytest.mark.parametrize("problem", ["missing-token", "authentication-not-selected"])
def test_creation_refuses_missing_or_ambiguous_authentication_before_add_or_sync(api, problem):
    api.session.get.side_effect = lambda model, _: api.account if model is PartnerAccount else None
    api.session.scalar.return_value = 0
    path, body = creation_request(
        api,
        "bearer" if problem == "missing-token" else "none",
        **({"credential": SECRET} if problem == "authentication-not-selected" else {}),
    )

    response = api.client.post(path, json=body)

    assert response.status_code == 422
    assert SECRET not in response.text
    api.session.add.assert_not_called()
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("api", ["admin"], indirect=True)
def test_duplicate_admin_connection_creation_requires_editing_the_existing_record(api):
    path, body = creation_request(api)
    body["provider"] = api.connection.provider

    response = api.client.post(path, json=body)

    assert response.status_code == 409 and "already exists" in response.json()["detail"]
    api.session.add.assert_not_called()
    api.command.assert_not_called()
    api.session.commit.assert_not_called()
