"""Partner adapters preserve scoped pages, typed public facts and tier authority.

Rows/counts below are already selected repository results. These tests do not
evaluate ownership or search predicates, page ordering, locks or transactions.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import partner_accounts as accounts
from devfeed_admin_api import partner_tools as tools
from devfeed_core import partner_connections
from devfeed_core.models import PartnerManualProduct
from devfeed_core.partner_connectors import ConnectorConfig
from devfeed_core.schemas import ArticleOut
from devfeed_http import partner_portal as portal
from devfeed_http.partner_campaigns import CampaignService
from devfeed_http.partner_portal import AssetOut, WorkspaceInventoryOut
from sqlalchemy.orm import Session
from test_admin_editorial_policy_unit import operator_api
from test_article_readiness_unit import processing_case as processing_case
from test_partner_connection_workflow_unit import connection
from test_partner_matching_workflow_unit import NOW, product
from test_partner_placement_workflow_unit import account, asset, campaign
from test_source_readiness_unit import configuration
from test_user_feed_read_remaining_unit import stories as stories


@pytest.mark.parametrize("query", ["", "Launch"])
def test_connection_page_projects_aggregate_counts_without_disclosing_saved_credentials(
    monkeypatch, query
):
    owner = account()
    saved = connection(account_id=owner.id, encrypted_credential="encrypted-private-credential")
    saved.connector = ConnectorConfig(
        base_url="https://imports.example", list_path="/catalog"
    ).model_dump(mode="json")
    settings = configuration()
    monkeypatch.setattr(partner_connections, "get_settings", lambda: settings)
    monkeypatch.setattr(partner_connections, "credential_storage_available", lambda: False)
    monkeypatch.setattr(
        tools,
        "paginate",
        Mock(
            return_value={
                "items": [saved],
                "total": 3,
                "limit": 1,
                "offset": 0,
            }
        ),
    )
    with operator_api(tools) as api:
        api.session.execute.return_value = [
            SimpleNamespace(
                provider=saved.provider,
                approval_mode="qualified",
                account_tier=owner.tier,
                syncing=False,
                products=4,
                qualified=2,
                checking=1,
                pending=2,
                excluded=0,
                commercial_approved=2,
            )
        ]
        response = api.client.get(
            "/v1/admin/partner-tools/connections", params={"q": query, "limit": 1}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert (page["total"], page["limit"], page["offset"]) == (3, 1, 0)
        [row] = page["items"]
        assert row["provider"] == saved.provider and row["state"] == "idle"
        assert (row["products"], row["qualified"], row["checking"], row["needs_attention"]) == (
            4,
            2,
            1,
            1,
        )
        assert row["credential_configured"] and not row["credentials_available"]
        assert "encrypted-private-credential" not in response.text
        api.session.commit.assert_not_called()


def test_connection_detail_uses_current_typed_projection_without_opening_a_sync(monkeypatch):
    owner, saved = account(), connection()
    saved.account_id = owner.id
    settings = configuration()
    monkeypatch.setattr(partner_connections, "get_settings", lambda: settings)
    monkeypatch.setattr(partner_connections, "credential_storage_available", lambda: False)
    with operator_api(tools) as api:
        api.session.get.return_value = saved
        api.session.execute.return_value = [
            SimpleNamespace(
                provider=saved.provider,
                approval_mode="qualified",
                account_tier=owner.tier,
                syncing=False,
                products=0,
                qualified=0,
                checking=0,
                pending=0,
                excluded=0,
                commercial_approved=0,
            )
        ]
        response = api.client.get(f"/v1/admin/partner-tools/connections/{saved.provider}")
        assert response.status_code == 200, response.text
        assert response.json()["provider"] == saved.provider
        assert response.json()["products"] == 0
        assert response.json()["next_sync_at"] == saved.next_sync_at.isoformat().replace(
            "+00:00", "Z"
        )
        api.session.add.assert_not_called()
        api.session.commit.assert_not_called()


def test_asset_detail_returns_the_selected_attribution_record_without_reporting_side_effects():
    owner, value = account(), product()
    current = asset(owner, value, name="API Checker recommendation")
    with operator_api(accounts) as api:
        api.session.scalar.side_effect = [owner, current]
        response = api.client.get(f"/v1/admin/partner-accounts/{owner.id}/assets/{current.id}")
        assert response.status_code == 200, response.text
        assert response.json() == AssetOut.model_validate(current).model_dump(mode="json")
        api.session.execute.assert_not_called()
        api.session.commit.assert_not_called()


def test_existing_paid_asset_cannot_be_edited_after_account_downgrade():
    owner, value = account(tier="bronze"), product()
    prior = asset(owner, value, name="Existing paid asset", kind="ad", automatic=False)
    with operator_api(accounts) as api:
        api.session.scalar.return_value = owner
        response = api.client.put(
            f"/v1/admin/partner-accounts/{owner.id}/assets/{prior.id}",
            json={"name": "Changed paid asset", "kind": "ad", "status": "active"},
        )
        assert response.status_code == 422
        assert (
            response.json()["detail"] == "Promotion type is not included in this partnership tier"
        )
        assert prior.name == "Existing paid asset"
        api.session.scalar.assert_called_once()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("empty", [False, True])
def test_campaign_page_keeps_total_and_zero_measurements_distinct_from_absent_page(empty):
    owner, session = account(), Mock(spec=Session)
    saved = campaign(owner, created_at=NOW - timedelta(days=1))
    session.scalars.return_value.all.return_value = [] if empty else [saved]
    session.scalar.return_value = 1
    session.execute.return_value = []
    page = CampaignService(session, owner).page(limit=1, offset=1 if empty else 0)
    assert page["total"] == 1
    if empty:
        assert page["items"] == []
        session.execute.assert_not_called()
    else:
        [row] = page["items"]
        assert row.id == saved.id and row.account_id == owner.id
        assert (row.impressions, row.clicks, row.ctr) == (0, 0, None)
    session.commit.assert_not_called()


@pytest.mark.parametrize("specific", [False, True])
def test_sponsorship_article_picker_returns_public_dtos_and_the_scoped_total(stories, specific):
    session, current = Mock(spec=Session), stories[0 if specific else 1]
    session.scalar.return_value = 1 if specific else 2
    session.scalars.return_value.all.return_value = [current]
    output = portal.sponsor_articles(
        session,
        q=current.title if specific else "",
        article_id=current.id if specific else None,
        offset=0 if specific else 1,
        limit=1,
    )
    assert output["total"] == (1 if specific else 2)
    [row] = output["items"]
    assert row == ArticleOut.from_article(current)
    assert "editorial_revision" not in row.model_dump()
    session.commit.assert_not_called()


def test_archived_direct_product_inventory_retains_editable_history_without_commercial_approval():
    owner, session = account(), Mock(spec=Session)
    value = product(
        status="withdrawn",
        verified_at=None,
        revision=3,
        updated_at=NOW,
        assessment={"state": "withdrawn"},
        metadata_account_id=owner.id,
    )
    manual = PartnerManualProduct(
        account_id=owner.id,
        product_id=value.id,
        active=False,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
    )
    session.scalar.return_value = 1
    session.execute.return_value.all.return_value = [(owner, value, None, manual, False)]
    page = portal.inventory_products(
        session, owner, q=value.name, product_id=value.id, limit=1, include_archived=True
    )
    result = WorkspaceInventoryOut.model_validate(page)
    assert (result.total, result.limit, result.offset) == (1, 1, 0)
    [row] = result.items
    assert row.account_id == owner.id and row.id == value.id
    assert row.source == "manual" and row.editable
    assert not row.active and not row.manual_active and not row.commercial_approved
    assert row.status == "withdrawn" and row.approval_source is None
    session.commit.assert_not_called()
