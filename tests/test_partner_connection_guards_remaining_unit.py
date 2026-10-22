"""Connection writes keep external logo preparation and credential failures bounded.

Authentication, request schemas, crypto and logo publication policy remain real.
Catalog reads and the uploader's prepared object receipt are service boundaries;
these units do not emulate PostgreSQL locking or object-storage compensation.
"""

import copy
import uuid
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from devfeed_admin_api import partner_tools as admin_tools
from devfeed_core.config import get_settings
from devfeed_core.models import PartnerAccount
from devfeed_core.partner_connection_logos import connection_logo_prefix, connection_platform
from devfeed_http.partner_connection_logos import ConnectionLogoEdit
from devfeed_partner_api import connections as partner_connections
from test_partner_connection_contracts_unit import api as api
from test_partner_connection_contracts_unit import (
    connection_output,
    connector,
    creation_request,
    update_request,
)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("problem", ["storage-unavailable", "invalid-token"])
def test_credential_failure_cannot_admit_an_import_or_replace_saved_connection(
    api, monkeypatch, operation, problem
):
    settings = get_settings()
    if problem == "storage-unavailable":
        monkeypatch.setattr(settings, "partner_credential_key", None)
    secret = "private\ninvalid-token" if problem == "invalid-token" else "private-valid-token"
    before = copy.deepcopy(
        (api.connection.connector, api.connection.revision, api.connection.encrypted_credential)
    )
    if operation == "create":
        api.session.get.side_effect = lambda model, _: (
            api.account if model is PartnerAccount else None
        )
        api.session.scalar.return_value = 0
        path, body = creation_request(api, "bearer", credential=secret)
        response = api.client.post(path, json=body)
    else:
        path, body = update_request(
            api, connector=connector("bearer").model_dump(mode="json"), credential=secret
        )
        response = api.client.put(path, json=body)
    assert response.status_code == (503 if problem == "storage-unavailable" else 422), response.text
    assert "private" not in response.text
    assert (
        api.connection.connector,
        api.connection.revision,
        api.connection.encrypted_credential,
    ) == before
    api.command.assert_not_called()
    api.session.add.assert_not_called()
    api.session.commit.assert_not_called()


def test_a_new_token_requires_an_explicit_authentication_scheme_on_settings_write(api):
    before = copy.deepcopy((api.connection.connector, api.connection.revision))
    path, body = update_request(api, credential="private-unused-token")
    response = api.client.put(path, json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == "Select authentication before providing a credential"
    assert "private" not in response.text
    assert (api.connection.connector, api.connection.revision) == before
    api.command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
def test_combined_settings_and_logo_edit_keeps_the_actual_logo_revision_and_upload_budget(
    api, monkeypatch, operation
):
    settings = get_settings()
    monkeypatch.setattr(settings, "image_public_url", "https://images.example")
    monkeypatch.setattr(settings, "image_storage_enabled", True)
    prepared = []
    remote_logo = "https://branding.example/platform.png"
    redis, limit = object(), Mock()
    monkeypatch.setattr(api.module, "get_redis", lambda: redis)
    monkeypatch.setattr(api.module, "limit_image_upload", limit)

    def editor(current, **values):
        edit = ConnectionLogoEdit(current, **values)
        assert edit.changed
        edit.prepare = Mock()

        @contextmanager
        def stage(session, authorize):
            # The external uploader has normalized and stored this managed
            # receipt. This is not a simulation of its guard or compensation.
            assert session is api.session and callable(authorize)
            edit.prepare.assert_called_once_with()
            prefix = connection_logo_prefix(current.provider)
            cache = uuid.uuid4().hex
            edit.published_url = f"https://images.example/{prefix}/64.webp?v={cache}"
            edit.asset = {
                "object_prefix": prefix,
                "cache_version": cache,
                "source_url": edit.published_url,
                "variants": [{"width": size} for size in (16, 32, 64, 96)],
            }
            yield edit

        edit.stage = stage
        prepared.append((current, edit))
        return edit

    def view(current):
        return connection_output(current, api.account).model_copy(
            update={"logo_url": connection_platform(current).logo_url}
        )

    monkeypatch.setattr(api.module, "ConnectionLogoEdit", editor)
    monkeypatch.setattr(admin_tools, "connection_view", lambda _session, current: view(current))
    monkeypatch.setattr(
        partner_connections, "connection_views", lambda _session, rows: [view(row) for row in rows]
    )
    if operation == "create":
        api.session.get.side_effect = lambda model, _: (
            api.account if model is PartnerAccount else None
        )
        api.session.scalar.return_value = 0
        path, body = creation_request(api, logo_url=remote_logo)
        response = api.client.post(path, json=body)
    else:
        path, body = update_request(api, logo_url=remote_logo)
        response = api.client.put(path, json=body)
    assert response.status_code == (201 if operation == "create" else 200), response.text
    ((current, edit),) = prepared
    assert current.logo_url == edit.published_url and current.managed_logo == edit.asset
    assert current.revision == (2 if operation == "create" else 4)
    assert response.json()["logo_url"] == edit.published_url
    assert remote_logo not in response.text
    limit.assert_called_once_with(redis, api.identity, api.account.id)
    api.command.assert_called_once()
    api.session.commit.assert_called_once()
