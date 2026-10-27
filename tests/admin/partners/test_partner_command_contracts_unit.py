"""Administrator partner commands preserve editor intent and current revisions.

Scoped rows and domain services are boundaries. No database eligibility, rollback,
concurrency, or upsert behavior is implemented by the session double.
"""

import asyncio
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_admin_api import partner_accounts as accounts
from devfeed_admin_api import partner_tools as tools
from devfeed_core import partner_tools
from devfeed_core.models import PartnerEvaluation
from devfeed_core.partner_connections import ProductAction
from devfeed_http.partner_portal import AccountInput, AssetInput, MetricInput
from fastapi import HTTPException
from sqlalchemy.orm import Session

from tests.partners.connections.test_connection_workflow_unit import connection, work
from tests.support.admin_editorial import identity
from tests.support.partner_accounts import account, asset
from tests.support.partner_matching import NOW, product, result


@pytest.fixture
def command(monkeypatch):
    value, session = product(), Mock(spec=Session)
    monkeypatch.setattr(tools, "utcnow", lambda: NOW)
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    monkeypatch.setattr(tools, "record", lambda *_: value)
    monkeypatch.setattr(tools, "listing_views", lambda *_: {value.id: []})
    monkeypatch.setattr(tools, "manual_product_views", lambda *_: {value.id: []})
    return value, session


@pytest.mark.parametrize("action", ["exclude", "retry"])
def test_product_command_retires_old_work_and_records_authenticated_revisioned_review(
    command, monkeypatch, action
):
    value, session = command
    active = work(connection(), operation="assess", product_id=value.id, status="running")
    evaluation = PartnerEvaluation(
        id=uuid.uuid4(),
        product_id=value.id,
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(minutes=1),
        available_at=NOW - timedelta(minutes=1),
        result={},
        snapshot={"version": "1", "product": partner_tools.product_snapshot(value), "articles": []},
        requested_by=tools.actor(identity()),
    )
    session.scalar.side_effect = [value.id, value]
    session.scalars.side_effect = [result([active]), result([evaluation])]
    monkeypatch.setattr(tools, "product_has_active_source", lambda *_: True)
    request = Mock()
    monkeypatch.setattr(tools, "request_assessment", request)
    output = tools.product_action(
        value.id, ProductAction(action=action, expected_revision=2), session, identity()
    )
    assert (value.status, value.excluded, value.revision) == (
        "paused" if action == "exclude" else "pending",
        action == "exclude",
        3,
    )
    assert value.verified_at is None and value.updated_at == NOW
    assert value.reviews[-1] == {
        "actor": tools.actor(identity()),
        "action": action,
        "note": "",
        "at": NOW.isoformat(),
    }
    assert all(
        job.status == "failed" and job.finished_at == NOW and job.lease_token is None
        for job in [active, evaluation]
    )
    assert output["revision"] == 3 and output["eligible"] is False
    assert request.call_count == (1 if action == "retry" else 0)
    session.commit.assert_called_once()


@pytest.mark.parametrize(
    "restriction",
    ["merged", "no_source", "inactive_source", "stale_revision"],
)
def test_product_commands_refuse_removed_ownership_or_stale_editor_snapshot(
    command, monkeypatch, restriction
):
    value, session = command
    initial_revision = value.revision
    current = value
    if restriction == "merged":
        value.merged_into_id, value.status = uuid.uuid4(), "withdrawn"
    session.scalar.side_effect = [None if restriction == "no_source" else value.id, current]
    session.scalars.side_effect = [result([]), result([])]
    monkeypatch.setattr(
        tools, "product_has_active_source", lambda *_: restriction != "inactive_source"
    )
    request = Mock()
    monkeypatch.setattr(tools, "request_assessment", request)
    with pytest.raises(HTTPException) as rejected:
        tools.product_action(
            value.id,
            ProductAction(
                action="retry",
                expected_revision=1 if restriction == "stale_revision" else initial_revision,
            ),
            session,
            identity(),
        )
    assert rejected.value.status_code == (404 if restriction in {"merged", "no_source"} else 409)
    assert value.revision == initial_revision and value.reviews == []
    request.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("change", ["retain", "new_override", "tier_violation"])
def test_account_update_preserves_omitted_relationship_and_revalidates_explicit_changes(
    monkeypatch, change
):
    owner, session = account(link_rel=["sponsored", "ugc"]), Mock(spec=Session)
    session.scalar.return_value = owner
    enroll = Mock()
    monkeypatch.setattr(accounts, "request_promotion_enrollment", enroll)
    values = {
        "name": "Renamed partner",
        "tier": "platinum" if change != "tier_violation" else "silver",
    }
    if change != "retain":
        values["link_rel"] = ["external"]
    payload = AccountInput(**values)
    if change == "tier_violation":
        with pytest.raises(HTTPException) as denied:
            accounts.update_account(owner.id, payload, identity(), session)
        assert denied.value.status_code == 422 and owner.name == "Launch platform"
        enroll.assert_not_called()
        session.commit.assert_not_called()
    else:
        assert accounts.update_account(owner.id, payload, identity(), session) is owner
        assert owner.name == "Renamed partner"
        assert owner.link_rel == (["sponsored", "ugc"] if change == "retain" else ["external"])
        enroll.assert_called_once_with(session, owner.id)
        session.commit.assert_called_once()


@pytest.mark.parametrize("change", ["name", "status", "product", "kind"])
def test_automatic_asset_can_change_presentation_state_but_never_attribution(change):
    owner, value, session = account(), product(), Mock(spec=Session)
    current = asset(owner, value)
    payload = AssetInput(
        name="Updated label",
        status="paused" if change == "status" else "active",
        kind="ad" if change == "kind" else "product",
        product_id=uuid.uuid4() if change == "product" else value.id,
    )
    session.scalar.side_effect = [owner, current]
    session.get.return_value = value
    if change in {"product", "kind"}:
        with pytest.raises(HTTPException) as denied:
            accounts.update_asset(owner.id, current.id, payload, identity(), session)
        assert denied.value.status_code == 409
        assert current.product_id == value.id and current.kind == "product"
        session.commit.assert_not_called()
    else:
        assert accounts.update_asset(owner.id, current.id, payload, identity(), session) is current
        assert current.name == payload.name and current.status == payload.status
        assert current.product_id == value.id and current.automatic
        session.commit.assert_called_once()


@pytest.mark.parametrize("tier", ["bronze", "silver"])
def test_manual_reporting_asset_requires_the_current_tier_even_for_admins(tier):
    owner, session = account(tier=tier), Mock(spec=Session)
    session.scalar.return_value = owner
    payload = AssetInput(name="Launch campaign", kind="ad", status="draft")
    if tier == "bronze":
        with pytest.raises(HTTPException) as denied:
            accounts.create_asset(owner.id, payload, identity(), session)
        assert denied.value.status_code == 422
        session.add.assert_not_called()
    else:
        saved = accounts.create_asset(owner.id, payload, identity(), session)
        assert saved.account_id == owner.id and saved.kind == "ad" and saved.status == "draft"
        session.add.assert_called_once_with(saved)
        session.commit.assert_called_once()


def test_trusted_metric_import_requests_replacement_for_exact_asset_and_day(monkeypatch):
    owner, value, session = account(), product(), Mock(spec=Session)
    current = asset(owner, value)
    session.scalar.side_effect = [owner, current, 73]
    payload = MetricInput(day=NOW.date() - timedelta(days=1), impressions=200, clicks=7)
    client = object()
    active = False

    @contextmanager
    def factory():
        nonlocal active
        active = True
        try:
            yield session
        finally:
            active = False

    async def write(_rows):
        assert not active, "PostgreSQL session held during analytics I/O"

    store = SimpleNamespace(partner_totals=AsyncMock(side_effect=write))
    construct = Mock(return_value=store)
    monkeypatch.setattr(accounts, "DirectEngagementAnalytics", construct)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(analytics=SimpleNamespace(client=client)))
    )
    response = asyncio.run(
        accounts.record_metrics(owner.id, current.id, payload, identity(), factory, request)
    )
    store.partner_totals.assert_awaited_once()
    requested = store.partner_totals.await_args.args[0]
    assert len(requested) == 1
    assert {name: requested[0][name] for name in ["asset_id", "day", "impressions", "clicks"]} == {
        "asset_id": current.id,
        **payload.model_dump(),
    }
    assert requested[0]["revision"] == 73
    assert requested[0]["updated_at"].utcoffset() == timedelta(0)
    assert response.status_code == 204
    session.commit.assert_not_called()
    construct.assert_called_once_with(client)


@pytest.mark.parametrize("provider", ["missing", "archived"])
def test_connection_controls_refuse_unavailable_imports_without_committing(provider):
    session = Mock(spec=Session)
    existing = connection(archived_at=NOW if provider == "archived" else None)
    session.get.return_value = None if provider == "missing" else existing
    session.scalar.return_value = existing
    with pytest.raises(HTTPException) as denied:
        tools.connection_action(
            provider, tools.ConnectionAction(action="sync"), session, identity()
        )
    assert denied.value.status_code == 404
    session.commit.assert_not_called()


def test_paused_connection_cannot_be_forced_to_sync():
    saved, session = connection(enabled=False), Mock(spec=Session)
    session.get.return_value = session.scalar.return_value = saved
    with pytest.raises(HTTPException, match="Enable this import"):
        tools.connection_action(
            saved.provider, tools.ConnectionAction(action="sync"), session, identity()
        )
    session.commit.assert_not_called()
