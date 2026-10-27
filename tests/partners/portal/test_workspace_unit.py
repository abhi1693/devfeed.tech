"""Workspace contracts enforce scope, bounds and independent product policy summaries."""

import uuid
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core.models import PartnerAccount, utcnow
from devfeed_http.partner_portal import inventory_product_view
from devfeed_partner_api import workspace
from devfeed_partner_api.auth import PartnerIdentity, require_partner
from devfeed_partner_api.connections import PartnerConnectionOut
from devfeed_partner_api.dependencies import get_session
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql


def statement_sql(statement):
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


@pytest.fixture
def api(monkeypatch):
    identity = PartnerIdentity(
        subject="workspace-user",
        issuer="https://workspace-issuer.example",
        organization_id="org",
        roles=["partner"],
        expires_at=2**31,
        csrf_token="test",
    )
    session = Mock()
    session.scalar.return_value = 2
    app = FastAPI()
    app.state.analytics_client = Mock()
    app.include_router(workspace.router)
    app.dependency_overrides[require_partner] = lambda: identity
    app.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(workspace, "credential_storage_available", lambda: True)
    with TestClient(app) as client:
        yield client, session, identity


def report_payload():
    today = utcnow().date()
    return {
        "start": today,
        "end": today,
        "totals": {"impressions": 0, "clicks": 0, "ctr": None, "measured_days": 0},
        "trend": [],
        "assets": [],
        "asset_total": 0,
        "last_updated_at": None,
        "format_totals": [],
    }


def connection_payload(account_id):
    return PartnerConnectionOut(
        account_id=account_id,
        provider="connection",
        name="Connection",
        api_url="https://catalog.example/products",
        sync_interval_minutes=360,
        revision=1,
        enabled=True,
        state="idle",
        last_sync_at=None,
        next_sync_at=None,
        error=None,
        products=0,
        qualified=0,
        checking=0,
        needs_attention=0,
        excluded=0,
        ai_enabled=False,
        auth_mode="none",
        credential_header="X-API-Key",
        credential_configured=False,
        credentials_available=True,
    )


def test_workspace_report_receives_complete_identity_scope_and_validated_filters(api, monkeypatch):
    client, session, identity = api
    reporter = AsyncMock(return_value=report_payload())
    monkeypatch.setattr(workspace, "reporting_metrics", reporter)
    monkeypatch.setattr(workspace, "require_partner_history", AsyncMock())
    result = client.get(
        "/v1/partner/dashboard?days=7&sort=account&order=desc&limit=3&offset=2&ad_format=feed"
    )
    assert result.status_code == 200 and result.json()["account_total"] == 2
    _, scope, _, days, limit, offset = reporter.call_args.args
    sql = statement_sql(scope)
    assert identity.subject in sql and identity.issuer in sql and "'active'" in sql
    assert "LIMIT" not in sql and "OFFSET" not in sql
    assert (days, limit, offset) == (7, 3, 2)
    assert reporter.call_args.kwargs == {"sort": "account", "order": "desc", "ad_format": "feed"}


def test_optional_account_filters_authorize_before_reading_data(api, monkeypatch):
    client, session, _ = api
    own = uuid.uuid4()
    access = Mock(return_value=NS(id=own))
    monkeypatch.setattr(workspace.PartnerAccountAccess, "require", access)
    reader = Mock(return_value={"items": [], "total": 0, "limit": 100, "offset": 0})
    monkeypatch.setattr(workspace, "inventory_page", reader)
    result = client.get(
        "/v1/partner/inventory",
        params={"account_id": str(own), "product_id": str(uuid.uuid4()), "q": "API"},
    )
    assert result.status_code == 200
    access.assert_called_once()
    assert str(own) in statement_sql(reader.call_args.args[1])
    assert reader.call_args.kwargs["q"] == "API"
    access.side_effect = HTTPException(404, "Partner account not found")
    for route in ("dashboard", "inventory", "connections"):
        assert (
            client.get(f"/v1/partner/{route}", params={"account_id": str(uuid.uuid4())}).status_code
            == 404
        )


@pytest.mark.parametrize("include_archived", [False, True])
def test_workspace_inventory_forwards_opt_in_archive_filter(api, monkeypatch, include_archived):
    client, session, identity = api
    reader = Mock(return_value={"items": [], "total": 0, "limit": 100, "offset": 0})
    monkeypatch.setattr(workspace, "inventory_page", reader)
    response = client.get(
        "/v1/partner/inventory", params={"include_archived": str(include_archived).lower()}
    )
    assert response.status_code == 200
    assert reader.call_args.kwargs["include_archived"] is include_archived
    scope = statement_sql(reader.call_args.args[1])
    assert identity.issuer in scope and identity.subject in scope and "'active'" in scope


def test_global_connection_list_uses_account_scope_and_batches_one_page(api, monkeypatch):
    client, session, identity = api
    account_id = uuid.uuid4()
    row = NS(account_name="Account name")
    connection = NS(account_id=account_id)

    class ConnectionRow:
        account_name = row.account_name

        def __getitem__(self, index):
            return connection

    session.execute.return_value.all.return_value = [ConnectionRow()]
    views = Mock(return_value=[connection_payload(account_id)])
    monkeypatch.setattr(workspace, "partner_connection_views", views)
    result = client.get("/v1/partner/connections?q=API&sort=-account&limit=3&offset=1")
    assert result.status_code == 200, result.text
    item = result.json()["items"][0]
    assert item["account_id"] == str(account_id) and item["account_name"] == "Account name"
    assert item["credentials_available"]
    sql = statement_sql(session.execute.call_args.args[0])
    assert identity.subject in sql and identity.issuer in sql
    assert "archived_at IS NULL" in sql and "partner_accounts.name DESC" in sql
    assert "LIMIT 3 OFFSET 1" in sql
    views.assert_called_once_with(session, [connection])
    assert client.get("/v1/partner/connections?sort=unsafe").status_code == 422


def test_connection_detail_and_jobs_resolve_account_from_authorized_provider(api, monkeypatch):
    client, session, identity = api
    account_id = uuid.uuid4()
    connection = NS(account_id=account_id)
    session.execute.return_value.one_or_none.return_value = (connection, "Authorized account")
    monkeypatch.setattr(
        workspace, "partner_connection_views", lambda *_: [connection_payload(account_id)]
    )
    result = client.get("/v1/partner/connections/connection")
    assert result.status_code == 200 and result.json()["account_name"] == "Authorized account"
    sql = statement_sql(session.execute.call_args.args[0])
    assert identity.subject in sql and "partner_connections.provider = 'connection'" in sql
    jobs = Mock(return_value={"items": [], "total": 0, "limit": 25, "offset": 0})
    monkeypatch.setattr(workspace, "connection_jobs", jobs)
    assert client.get("/v1/partner/connections/connection/jobs").status_code == 200
    assert jobs.call_args.args[:2] == (account_id, "connection")
    session.execute.return_value.one_or_none.return_value = None
    for suffix in ("", "/jobs"):
        assert client.get("/v1/partner/connections/foreign" + suffix).status_code == 404


def test_global_create_forwards_only_connection_fields_to_locked_account_service(api, monkeypatch):
    client, _, _ = api
    account_id = uuid.uuid4()
    create = Mock(return_value=connection_payload(account_id))
    monkeypatch.setattr(workspace, "create_connection", create)
    monkeypatch.setattr(
        workspace.PartnerAccountAccess, "require", lambda *_: NS(name="Authorized account")
    )
    body = {
        "account_id": str(account_id),
        "name": "Catalog",
        "enabled": False,
        "connector": {"base_url": "https://catalog.example"},
    }
    result = client.post("/v1/partner/connections", json=body)
    assert result.status_code == 201
    assert create.call_args.args[0] == account_id
    assert "account_id" not in type(create.call_args.args[1]).model_fields
    assert (
        client.post(
            "/v1/partner/connections", json={**body, "approval_mode": "trusted"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "path",
    [
        "dashboard?days=0",
        "dashboard?days=366",
        "dashboard?limit=201",
        "dashboard?offset=-1",
        "dashboard?ad_format=invalid",
        "dashboard?sort=invalid",
        "inventory?limit=201",
        "inventory?offset=-1",
        "inventory?include_archived=maybe",
        "connections?limit=101",
        "connections?offset=-1",
    ],
)
def test_global_reads_reject_unbounded_or_invalid_parameters(api, path):
    client, session, _ = api
    assert client.get("/v1/partner/" + path).status_code == 422
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "mode,accepted",
    [
        ("qualified", True),
        ("expired", False),
        ("trusted", True),
        ("untrusted", False),
        ("wrong-source", False),
        ("excluded", False),
        ("global-excluded", False),
        ("merged", False),
    ],
)
def test_inventory_policy_summary_uses_loaded_account_product_policy(mode, accepted):
    now = utcnow()
    account = PartnerAccount(
        id=uuid.uuid4(),
        name="Account",
        tier="platinum",
        link_rel=["nofollow"],
        approval_mode="trusted" if mode in {"trusted", "wrong-source", "excluded"} else "qualified",
    )
    product = NS(
        id=uuid.uuid4(),
        name="Product",
        product_url="https://product.example/",
        tagline="A useful developer product.",
        pricing="paid",
        assessment={},
        revision=1,
        technologies=["OpenAPI"],
        status="approved"
        if mode in {"qualified", "expired", "global-excluded", "merged"}
        else "pending",
        verified_at=now - timedelta(days=91) if mode == "expired" else now,
        excluded=mode == "global-excluded",
        merged_into_id=uuid.uuid4() if mode == "merged" else None,
    )
    policy = NS(
        excluded=mode == "excluded",
        commercial_approved_at=now,
        approval_source="qualified" if mode == "wrong-source" else "trusted",
        ad_formats=["feed"],
        promotion_surfaces=["article"],
        ad_image_url=None,
        link_rel=["sponsored"],
    )
    data = inventory_product_view(account, product, policy, now)
    assert data["commercial_approved"] is accepted
    assert data["account_id"] == account.id and data["account_name"] == account.name
    assert data["ad_formats"] == ["feed"] and data["promotion_surfaces"] == ["article"]
    assert data["link_rel"] == data["effective_link_rel"] == ["sponsored"]
    assert data["configurable_link_rel"] is True
    assert data["link_rel_options"] == ["nofollow", "sponsored", "ugc", "external"]


def test_inventory_without_policy_inherits_account_defaults_without_querying():
    now = utcnow()
    account = PartnerAccount(
        id=uuid.uuid4(), name="Account", approval_mode="trusted", tier="gold", link_rel=["nofollow"]
    )
    product = NS(
        id=uuid.uuid4(),
        name="Product",
        product_url="https://product.example/",
        tagline="A useful developer product.",
        pricing="paid",
        assessment={},
        revision=1,
        technologies=[],
        status="pending",
        verified_at=None,
        excluded=False,
        merged_into_id=None,
    )
    result = inventory_product_view(account, product, None, now)
    assert not result["excluded"] and not result["commercial_approved"]
    assert result["ad_formats"] is None and result["promotion_surfaces"] is None
    assert result["link_rel"] is None and result["effective_link_rel"] == ["nofollow"]
    assert result["configurable_link_rel"] is False
    assert result["link_rel_options"] == ["nofollow", "sponsored", "ugc", "external"]
