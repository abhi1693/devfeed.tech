"""Portal adapters keep member authority and explicit service scopes on every call.

Domain services and scoped page results are boundaries, with no SQL membership,
page selection, upload compensation or transaction semantics implemented here.
"""

from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core.partner_connections import ConnectionOut
from devfeed_core.partner_connectors import ConnectorConfig
from devfeed_http.pagination import ListQuery
from devfeed_http.partner_portal import (
    AudienceTopicsOut,
    CreativePreviewOut,
    Dashboard,
    DestinationPreviewOut,
    InventoryOut,
    ProductPromotionInput,
    _product_promotion_out,
)
from devfeed_partner_api import connections, portal
from fastapi import HTTPException
from sqlalchemy.orm import Session

from tests.admin.partners.test_partner_authority_boundaries_unit import (
    uploaded_document,
    uploaded_policy,
)
from tests.partners.catalog.test_catalog_commands_unit import policy
from tests.partners.connections.test_connection_workflow_unit import connection
from tests.partners.portal.auth.test_api_policy_boundary_unit import identity
from tests.support.partner_accounts import account
from tests.support.partner_matching import NOW, product


@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize(
    "read", ["inventory", "audience", "destination", "creative", "product", "dashboard"]
)
def test_portal_reads_require_the_current_member_account_before_forwarding_exact_scope(
    monkeypatch, denied, read
):
    owner, value, session = account(), product(), Mock(spec=Session)
    access = Mock()
    access.require.return_value = owner
    if denied:
        access.require.side_effect = HTTPException(404, "Partner account not found")
    construct = Mock(return_value=access)
    monkeypatch.setattr(portal, "PartnerAccountAccess", construct)
    name, execute, expected_args, expected_kwargs, output = {
        "inventory": (
            "inventory_products",
            lambda: portal.inventory(
                owner.id,
                session,
                identity(),
                q="OpenAPI",
                product_id=value.id,
                offset=0,
                limit=12,
                include_archived=True,
            ),
            (session, owner),
            {
                "q": "OpenAPI",
                "product_id": value.id,
                "offset": 0,
                "limit": 12,
                "include_archived": True,
            },
            InventoryOut(items=[], total=0),
        ),
        "audience": (
            "audience_topic_options",
            lambda: portal.audience_topics(
                owner.id,
                session,
                identity(),
                "tools",
                q="OpenAPI",
                topic_id=None,
                offset=0,
                limit=12,
            ),
            (session, "tools"),
            {"account": owner, "q": "OpenAPI", "topic_id": None, "offset": 0, "limit": 12},
            AudienceTopicsOut(items=[], total=0),
        ),
        "destination": (
            "destination_preview",
            lambda: portal.preview_destination(owner.id, session, identity()),
            (session, owner),
            {},
            DestinationPreviewOut(product=None),
        ),
        "creative": (
            "creative_preview",
            lambda: portal.preview_creative(owner.id, session, identity(), value.id),
            (session, owner),
            {"product_id": value.id},
            CreativePreviewOut(product=None),
        ),
        "product": (
            "product_promotion",
            lambda: portal.get_product_promotion(owner.id, value.id, session, identity()),
            (session, owner, value.id),
            {},
            _product_promotion_out(session, owner, policy(owner, value), value),
        ),
        "dashboard": (
            "reporting_dashboard",
            lambda: portal.dashboard(
                owner.id,
                session,
                identity(),
                days=7,
                limit=12,
                offset=0,
                sort="clicks",
                order="desc",
                ad_format="feed",
            ),
            (owner.id, session, owner, 7, 12, 0),
            {"sort": "clicks", "order": "desc", "ad_format": "feed"},
            Dashboard(
                account=owner,
                start=(NOW - timedelta(days=6)).date(),
                end=NOW.date(),
                totals={"impressions": 0, "clicks": 0, "ctr": None, "measured_days": 0},
                trend=[],
                assets=[],
                asset_total=0,
                last_updated_at=None,
            ),
        ),
    }[read]
    service = Mock(return_value=output)
    monkeypatch.setattr(portal, name, service)
    if denied:
        with pytest.raises(HTTPException) as error:
            execute()
        assert error.value.status_code == 404
        service.assert_not_called()
    else:
        assert execute() is output
        service.assert_called_once_with(*expected_args, **expected_kwargs)
    construct.assert_called_once_with(session, identity())
    access.require.assert_called_once_with(owner.id)
    session.commit.assert_not_called()


@pytest.mark.parametrize("upload", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
def test_portal_artwork_write_uses_product_scoped_member_authority(monkeypatch, upload, revoked):
    owner, value, session = account(), product(), Mock(spec=Session)
    access = Mock()
    access.require.return_value = owner
    access.lock_product.return_value = owner
    if revoked:
        access.lock_product.side_effect = HTTPException(404, "Partner account not found")
    monkeypatch.setattr(portal, "PartnerAccountAccess", Mock(return_value=access))
    saved = uploaded_policy(owner, value) if upload else policy(owner, value, excluded=True)
    output = _product_promotion_out(
        session, owner, saved, value, status="ready" if upload else "default"
    )
    service = Mock(return_value=output)
    monkeypatch.setattr(
        portal,
        "upload_product_ad_image" if upload else "save_product_promotion_with_images",
        service,
    )
    redis, limit = object(), Mock()
    monkeypatch.setattr(portal, "get_redis", lambda: redis)
    monkeypatch.setattr(portal, "limit_image_upload", limit)
    document = uploaded_document() if upload else ProductPromotionInput(excluded=True)

    def execute():
        if upload:
            return portal.upload_ad_image(owner.id, value.id, document, session, identity())
        return portal.update_product_promotion(owner.id, value.id, document, session, identity())

    if revoked:
        with pytest.raises(HTTPException) as error:
            execute()
        assert error.value.status_code == 404
        service.assert_not_called()
    else:
        assert execute() is output
        service.assert_called_once_with(session, owner, value.id, document)
    access.lock_product.assert_called_once_with(owner.id, value.id)
    if upload:
        access.require.assert_called_once_with(owner.id)
        limit.assert_called_once_with(redis, identity(), owner.id)
    else:
        access.require.assert_not_called()
        limit.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("q", ["", "Launch"])
def test_connection_page_preserves_the_scoped_page_and_public_credential_capability(
    monkeypatch, configured, q
):
    owner, session, access = account(), Mock(spec=Session), Mock()
    access.require.return_value = owner
    saved = connection(account_id=owner.id)
    saved.connector = ConnectorConfig(
        base_url="https://imports.example", list_path="/catalog"
    ).model_dump(mode="json")
    # Aggregate and settings projection are independent read boundaries; the
    # actual portal projection below keeps upstream diagnostics private.
    view = ConnectionOut(
        provider=saved.provider,
        name=saved.name,
        account_id=owner.id,
        account_tier=owner.tier,
        api_url="https://imports.example/catalog",
        connector=saved.connector,
        enabled=True,
        sync_interval_minutes=360,
        revision=3,
        state="idle",
        last_sync_at=saved.last_sync_at,
        next_sync_at=saved.next_sync_at,
        error=None,
        products=3,
        qualified=2,
        checking=0,
        needs_attention=1,
        excluded=0,
        ai_enabled=True,
    )
    monkeypatch.setattr(connections, "PartnerAccountAccess", Mock(return_value=access))
    monkeypatch.setattr(connections, "credential_storage_available", lambda: configured)
    monkeypatch.setattr(connections, "connection_views", Mock(return_value=[view]))
    page = {"items": [saved], "total": 3, "limit": 1, "offset": 0}
    paginate = Mock(return_value=page)
    monkeypatch.setattr(connections, "paginate", paginate)
    query = ListQuery(q=q, limit=1, offset=0)
    output = connections.list_connections(owner.id, session, identity(), query)
    assert output["credentials_available"] is configured
    assert (output["total"], output["limit"], output["offset"]) == (3, 1, 0)
    (item,) = output["items"]
    assert item.provider == saved.provider and item.account_id == owner.id
    assert item.auth_mode == "none" and item.api_url == "https://imports.example/catalog"
    access.require.assert_called_once_with(owner.id)
    assert paginate.call_args.args[0] is session and paginate.call_args.args[2] is query
    assert "encrypted_credential" not in item.model_dump()
    session.commit.assert_not_called()
