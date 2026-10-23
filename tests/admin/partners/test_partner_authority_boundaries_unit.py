"""Admin partner adapters retain scoped authority across domain and upload boundaries.

Services supply validated domain results. The session double does not emulate
database filtering, locking, transactions, or object-storage compensation.
"""

import copy
import uuid
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from devfeed_admin_api import partner_accounts as accounts
from devfeed_admin_api import partner_tools as tools
from devfeed_core import partner_credentials
from devfeed_core.managed_images import IMAGE_VERSION
from devfeed_core.partner_connections import ConnectionSettings
from devfeed_core.partner_connectors import ConnectorConfig
from devfeed_core.partner_product_images import AD_IMAGE_SIZES, ad_image_prefix
from devfeed_http.partner_connection_logos import ConnectionLogoEdit
from devfeed_http.partner_portal import (
    CampaignInput,
    CampaignOut,
    InventoryOut,
    ProductPromotionInput,
    _product_promotion_out,
)
from fastapi import HTTPException, UploadFile
from PIL import Image
from pydantic import SecretStr
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from tests.partners.catalog.test_catalog_commands_unit import policy
from tests.partners.connections.test_connection_workflow_unit import connection
from tests.support.admin_editorial import identity, operator_api
from tests.support.partner_accounts import account, campaign
from tests.support.partner_matching import product
from tests.support.source_readiness import configuration


def uploaded_document():
    body = BytesIO()
    Image.new("RGB", (1200, 800), "white").save(body, format="PNG")
    body.seek(0)
    return UploadFile(body, filename="creative.png", headers=Headers({"content-type": "image/png"}))


def uploaded_policy(owner, value):
    prefix, cache = ad_image_prefix(owner.id, value.id), uuid.uuid4().hex
    source = f"https://images.example/{prefix}/800.webp?v={cache}"
    return policy(
        owner,
        value,
        ad_image_url=source,
        ad_image_revision=1,
        managed_ad_image={
            "upload": True,
            "object_product_id": str(value.id),
            "object_prefix": prefix,
            "cache_version": cache,
            "source_url": source,
            "subject_revision": 1,
            "version": IMAGE_VERSION,
            "variants": [
                {"key": f"{prefix}/{width}.webp", "width": width} for width in AD_IMAGE_SIZES
            ],
        },
    )


@pytest.fixture
def scoped(monkeypatch):
    owner, session = account(), Mock(spec=Session)
    access = Mock(return_value=owner)
    monkeypatch.setattr(accounts, "account_access", access)
    return owner, session, access


@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
def test_admin_campaign_routes_require_superuser_before_domain_or_body_work(monkeypatch, method):
    service = Mock()
    monkeypatch.setattr(accounts, "CampaignService", service)
    with operator_api(accounts) as api:
        api.admin.roles = ["editor"]
        path = f"/v1/admin/partner-accounts/{uuid.uuid4()}/campaigns"
        if method == "PUT":
            path += f"/{uuid.uuid4()}"
        response = api.client.request(method, path, json={} if method != "GET" else None)
        assert response.status_code == 403
        assert response.json()["detail"] == "Superuser access required"
        service.assert_not_called()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("command", ["list", "get", "create", "update"])
def test_campaign_adapters_keep_account_scope_explicit_settings_and_commit_order(
    scoped, monkeypatch, command
):
    owner, session, access = scoped
    saved = campaign(owner)
    report = CampaignOut.model_validate(saved)
    body = CampaignInput.model_validate(
        report.model_dump(exclude={"id", "account_id", "managed", "impressions", "clicks", "ctr"})
    )
    service = Mock()
    service.page.return_value = {"items": [report], "total": 3}
    service.get.return_value = report
    service.save.return_value = saved

    def reports(rows):
        session.commit.assert_called_once_with()
        assert rows == [saved]
        return [report]

    service.reports.side_effect = reports
    construct = Mock(return_value=service)
    monkeypatch.setattr(accounts, "CampaignService", construct)
    if command == "list":
        output = accounts.campaigns(owner.id, session, identity(), limit=1, offset=2)
        assert output == {"items": [report], "total": 3}
        service.page.assert_called_once_with(limit=1, offset=2)
    elif command == "get":
        assert accounts.campaign(owner.id, saved.id, session, identity()) is report
        service.get.assert_called_once_with(saved.id)
    elif command == "create":
        assert accounts.create_campaign(owner.id, body, session, identity()) is saved
        service.save.assert_called_once_with(body)
    else:
        assert accounts.update_campaign(owner.id, saved.id, body, session, identity()) is report
        service.save.assert_called_once_with(body, campaign_id=saved.id)
    construct.assert_called_once_with(session, owner)
    access.assert_called_once_with(
        session, identity(), owner.id, **({"lock": True} if command in {"create", "update"} else {})
    )
    assert session.commit.call_count == int(command in {"create", "update"})


@pytest.mark.parametrize("include_archived", [False, True])
def test_inventory_adapter_preserves_explicit_account_and_editor_filters(
    scoped, monkeypatch, include_archived
):
    owner, session, access = scoped
    identifier = uuid.uuid4()
    output = InventoryOut(items=[], total=0)
    read = Mock(return_value=output)
    monkeypatch.setattr(accounts, "inventory_products", read)
    assert (
        accounts.inventory(
            owner.id,
            session,
            identity(),
            limit=12,
            offset=24,
            q="OpenAPI",
            product_id=identifier,
            include_archived=include_archived,
        )
        is output
    )
    read.assert_called_once_with(
        session,
        owner,
        q="OpenAPI",
        product_id=identifier,
        offset=24,
        limit=12,
        include_archived=include_archived,
    )
    access.assert_called_once_with(session, identity(), owner.id)
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "action,removed_after_read",
    [
        ("get", False),
        ("list", False),
        ("update", False),
        ("update", True),
        ("upload", False),
        ("upload", True),
    ],
)
def test_product_artwork_adapters_recheck_account_after_subject_preparation(
    scoped, monkeypatch, action, removed_after_read
):
    owner, session, access = scoped
    value = product()
    body = ProductPromotionInput(ad_image_url="https://creative.example/custom.png")
    saved_policy = (
        uploaded_policy(owner, value)
        if action == "upload"
        else policy(
            owner,
            value,
            **(
                {"ad_image_url": body.ad_image_url, "ad_image_revision": 1}
                if action == "update"
                else {}
            ),
        )
    )
    output = _product_promotion_out(
        session,
        owner,
        saved_policy,
        value,
        status="ready" if action == "upload" else "queued" if action == "update" else "default",
    )
    upload = uploaded_document()
    read = Mock(return_value=output)
    listing = Mock(return_value=[output])
    save, store = Mock(return_value=output), Mock(return_value=output)
    monkeypatch.setattr(accounts, "product_promotion", read)
    monkeypatch.setattr(accounts, "list_product_promotions", listing)
    monkeypatch.setattr(accounts, "save_product_promotion_with_images", save)
    monkeypatch.setattr(accounts, "upload_product_ad_image", store)
    product_lock = Mock()
    account_lock = Mock(return_value=None if removed_after_read else owner)
    monkeypatch.setattr(accounts, "lock_image_product", product_lock)
    monkeypatch.setattr(accounts, "lock_image_account", account_lock)
    redis, limit = object(), Mock()
    monkeypatch.setattr(accounts, "get_redis", lambda: redis)
    monkeypatch.setattr(accounts, "limit_image_upload", limit)
    if action == "get":
        assert accounts.get_product_promotion(owner.id, value.id, session, identity()) is output
        read.assert_called_once_with(session, owner, value.id)
    elif action == "list":
        assert accounts.product_promotions(owner.id, session, identity()) == [output]
        listing.assert_called_once_with(session, owner.id)
    else:

        def execute():
            if action == "update":
                return accounts.update_product_promotion(
                    owner.id, value.id, body, session, identity()
                )
            return accounts.upload_ad_image(owner.id, value.id, upload, session, identity())

        if removed_after_read:
            with pytest.raises(HTTPException) as error:
                execute()
            assert error.value.status_code == 404
            save.assert_not_called()
            store.assert_not_called()
        else:
            assert execute() is output
            boundary = save if action == "update" else store
            boundary.assert_called_once_with(
                session, owner, value.id, body if action == "update" else upload
            )
        product_lock.assert_called_once_with(session, owner.id, value.id)
        account_lock.assert_called_once()
    access.assert_called_once_with(session, identity(), owner.id)
    assert limit.call_args_list == (
        [] if action != "upload" else [((redis, identity(), owner.id), {})]
    )
    session.commit.assert_not_called()


def logo_boundary(monkeypatch, *, changed=False):
    prepared = Mock(changed=changed)

    @contextmanager
    def stage(_session, authorize):
        # External uploader boundary only: no object/transaction behavior is claimed.
        assert callable(authorize)
        yield

    prepared.stage.side_effect = stage
    monkeypatch.setattr(tools, "ConnectionLogoEdit", lambda *_args, **_kwargs: prepared)
    return prepared


@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("failure", ["none", "prepare", "removed", "revision"])
def test_admin_logo_rechecks_current_revision_after_external_preparation_without_losing_saved_logo(
    monkeypatch, owner, failure
):
    saved = connection(account_id=uuid.uuid4() if owner else None)
    saved.logo_url = "https://creative.example/old.png"
    saved.updated_by = tools.actor(identity().model_copy(update={"subject": "previous-editor"}))
    before = copy.deepcopy((saved.logo_url, saved.updated_by))
    session = Mock(spec=Session)
    clear_logo = ConnectionLogoEdit(saved, source_url=None)
    prepared = logo_boundary(monkeypatch, changed=clear_logo.changed)
    reads = [saved, saved, saved.account_id, saved] if owner else [saved, saved, saved]
    session.scalar.side_effect = reads
    redis, limit = object(), Mock()
    monkeypatch.setattr(tools, "get_redis", lambda: redis)
    monkeypatch.setattr(tools, "limit_image_upload", limit)
    monkeypatch.setattr(tools, "connection_view", lambda *_: saved)

    def prepare():
        session.rollback.assert_called_once_with()
        if failure == "prepare":
            raise HTTPException(422, "Image normalization failed")
        if failure == "removed":
            session.scalar.side_effect = [None]
        elif failure == "revision":
            saved.revision += 1

    prepared.prepare.side_effect = prepare
    prepared.apply.side_effect = clear_logo.apply
    if failure == "none":
        assert (
            tools.save_connection_logo(saved.provider, 3, session, identity(), source_url=None)
            is saved
        )
        assert saved.logo_url is None and saved.updated_by == tools.actor(identity())
        assert saved.managed_logo == {} and saved.revision == 4
        prepared.apply.assert_called_once_with(saved)
        session.commit.assert_called_once_with()
    else:
        with pytest.raises(HTTPException) as error:
            tools.save_connection_logo(saved.provider, 3, session, identity(), source_url=None)
        assert error.value.status_code == {"prepare": 422, "removed": 404, "revision": 409}[failure]
        assert (saved.logo_url, saved.updated_by) == before
        assert saved.revision == (4 if failure == "revision" else 3)
        prepared.apply.assert_not_called()
        session.commit.assert_not_called()
    limit.assert_called_once_with(redis, identity(), saved.account_id or saved.provider)


@pytest.mark.parametrize("move", ["unassign", "missing", "replacement", "credential-bound"])
def test_admin_connection_move_requires_existing_owner_and_explicit_credential_rebinding(
    monkeypatch, move
):
    original, destination = account(), account()
    saved = connection(account_id=original.id, encrypted_credential=None)
    session = Mock(spec=Session)
    session.scalar.return_value = saved
    session.get.return_value = None if move == "missing" else destination
    config = ConnectorConfig(base_url="https://imports.example", auth={"mode": "bearer"})
    saved.connector = config.model_dump(mode="json")
    settings = configuration(partner_credential_key=SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(partner_credentials, "get_settings", lambda: settings)
    saved.encrypted_credential = partner_credentials.encrypt_credential(
        saved, config, "old-import-key"
    )
    before = saved.encrypted_credential
    logo_boundary(monkeypatch)
    monkeypatch.setattr(tools, "lock_catalog", Mock())
    monkeypatch.setattr(tools, "connection_view", lambda *_: saved)
    configure, definition = Mock(return_value=[]), Mock(return_value=True)
    monkeypatch.setattr(tools, "configure_connection", configure)
    monkeypatch.setattr(tools, "update_connection_definition", definition)
    values = {
        "expected_revision": 3,
        "enabled": False,
        "account_id": None if move == "unassign" else destination.id,
    }
    if move == "unassign":
        values["connector"] = config.model_copy(
            update={"auth": config.auth.model_copy(update={"mode": "none"})}
        )
    elif move in {"replacement", "missing"}:
        values["credential"] = SecretStr("new-import-key")
    body = ConnectionSettings(**values)
    if move in {"credential-bound", "missing"}:
        with pytest.raises(HTTPException) as error:
            tools.update_connection(saved.provider, body, session, identity())
        assert error.value.status_code == (409 if move == "credential-bound" else 422)
        assert saved.account_id == original.id and saved.revision == 3
        assert saved.encrypted_credential == before
        configure.assert_not_called()
        session.commit.assert_not_called()
    else:
        assert tools.update_connection(saved.provider, body, session, identity()) is saved
        assert saved.account_id == (None if move == "unassign" else destination.id)
        assert saved.revision == 4 and saved.updated_by == tools.actor(identity())
        if move == "replacement":
            assert partner_credentials.connection_credential(saved, config) == "new-import-key"
            assert saved.encrypted_credential != before
        else:
            assert saved.encrypted_credential is None
        definition.assert_called_once()
        configure.assert_called_once()
        session.commit.assert_called_once_with()
