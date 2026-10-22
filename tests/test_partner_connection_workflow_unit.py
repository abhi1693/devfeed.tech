"""Connection commands consume scoped repository reads and preserve durable work.

SQL membership, concurrency and uniqueness are integration contracts; no database
filter or transaction implementation is reproduced here.
"""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core import partner_connections as connections
from devfeed_core import partner_credentials, partner_tools
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import PartnerAccount, PartnerEvaluation, PartnerPipelineJob
from devfeed_core.partner_connections import ConnectionCreate
from devfeed_core.partner_connectors import ConnectorConfig, connector_snapshot
from devfeed_core.partner_tools import ProductInput, article_snapshot, product_snapshot
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_partner_matching_workflow_unit import NOW, article, product, result
from test_source_readiness_unit import configuration


def connection(**changes):
    from devfeed_core.models import PartnerConnection

    return PartnerConnection(
        **{
            "provider": "nick-launches",
            "name": "Launch platform",
            "enabled": True,
            "archived_at": None,
            "revision": 3,
            "sync_revision": 2,
            "connector": {},
            "sync_interval_minutes": 360,
            "last_sync_at": NOW - timedelta(days=1),
            "next_sync_at": NOW,
            "last_error": None,
            "managed_logo": {},
            **changes,
        }
    )


def work(connection, *, operation="sync", status="queued", **changes):
    job = PartnerPipelineJob(
        **{
            "id": uuid.uuid4(),
            "provider": connection.provider,
            "operation": operation,
            "status": status,
            "attempts": 0,
            "created_at": NOW - timedelta(minutes=10),
            "available_at": NOW - timedelta(minutes=10),
            "dispatched_at": None,
            "payload": {
                "pipeline_version": 2,
                "connection_revision": connection.sync_revision,
                "generation": str(uuid.uuid4()),
                "cursor": "page-2",
                "pages": 1,
                "cursors": ["page-2"],
                "connector": connector_snapshot(connection),
            },
            **changes,
        }
    )
    if operation == "assess":
        job.payload = {"revision": 2}
    elif operation == "sync_product":
        candidate = ProductInput(
            provider=connection.provider,
            external_id=job.external_id,
            name="API Checker",
            product_url="https://checker.example/",
            listing_url="https://nicklaunches.com/products/checker/",
            tagline="Detect breaking OpenAPI changes before deployment.",
            pricing="free",
        )
        job.payload = {"candidate": candidate.model_dump(mode="json")}
    if status == "running":
        start_job(job, NOW - timedelta(seconds=10), 300)
    elif status == "failed":
        job.attempts, job.finished_at, job.error = (
            3,
            NOW - timedelta(minutes=1),
            "Upstream unavailable",
        )
    return job


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(connections, "utcnow", lambda: NOW)
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    settings = configuration()
    monkeypatch.setattr(connections, "get_settings", lambda: settings)
    monkeypatch.setattr(partner_credentials, "get_settings", lambda: settings)


@pytest.mark.parametrize("provider", ["new", "Uppercase", "invalid_slug", "bad--slug"])
def test_connection_identifiers_reject_reserved_or_non_slug_input(provider):
    with pytest.raises(ValidationError, match="lowercase slug"):
        ConnectionCreate(provider=provider)


def test_active_sync_is_coalesced_without_resetting_its_progress():
    saved = connection()
    running = work(saved, status="running")
    session = Mock(spec=Session)
    session.scalar.return_value = running
    previous = copy.deepcopy(running.payload)
    assert connections.request_sync(session, saved) is running
    assert running.payload == previous and running.status == "running" and running.attempts == 1
    session.add.assert_not_called()


def test_one_interrupted_generation_resumes_failed_children_without_losing_cursor():
    saved = connection(last_error="Upstream unavailable")
    previous = work(saved, status="failed")
    child = work(
        saved,
        operation="sync_product",
        status="failed",
        parent_id=previous.id,
        external_id="checker",
    )
    session = Mock(spec=Session)
    session.scalar.side_effect = [None, previous]
    session.scalars.return_value = [child]
    assert connections.request_sync(session, saved) is previous
    assert previous.payload["cursor"] == "page-2" and previous.payload["resumptions"] == 1
    for job in [previous, child]:
        assert (job.status, job.attempts, job.error) == ("queued", 0, None)
        assert job.available_at == NOW and job.finished_at is None and job.dispatched_at is None
    assert saved.last_error is None and saved.next_sync_at == NOW + timedelta(hours=6)
    session.add.assert_not_called()


@pytest.mark.parametrize("reason", ["revision", "resumed", "legacy"])
def test_unresumable_generation_fences_old_children_and_starts_fresh_scan(reason):
    saved = connection()
    previous = work(saved, status="failed")
    if reason == "revision":
        previous.payload = {**previous.payload, "connection_revision": saved.sync_revision - 1}
    elif reason == "resumed":
        previous.payload = {**previous.payload, "resumptions": 1}
    else:
        previous.payload = {"pipeline_version": 1}
    child = work(
        saved,
        operation="sync_product",
        status="running",
        parent_id=previous.id,
        external_id="checker",
    )
    session = Mock(spec=Session)
    session.scalar.side_effect = [None, previous]
    session.scalars.return_value = [child]
    fresh = connections.request_sync(session, saved)
    assert fresh is not previous and fresh.operation == "sync"
    assert fresh.payload["cursor"] is None and fresh.payload["pages"] == 0
    assert fresh.payload["connection_revision"] == saved.sync_revision
    assert uuid.UUID(fresh.payload["generation"])
    assert (child.status, child.finished_at, child.lease_token) == ("failed", NOW, None)
    assert child.error == "Superseded by a fresh sync generation"
    session.add.assert_called_once_with(fresh)


def test_pause_cancels_imports_but_preserves_assessment_with_another_active_source(monkeypatch):
    saved = connection()
    importing = work(saved, status="running")
    child = work(saved, operation="sync_product", external_id="checker", parent_id=importing.id)
    shared, orphan = product(), product()
    shared_job = work(saved, operation="assess", product_id=shared.id)
    orphan_job = work(saved, operation="assess", product_id=orphan.id, status="running")
    session = Mock(spec=Session)
    session.scalars.return_value = result([importing, child, shared_job, orphan_job])
    monkeypatch.setattr(
        connections,
        "product_has_active_source",
        lambda _session, identifier: identifier == shared.id,
    )
    affected = connections.configure_connection(session, saved, False)
    assert set(affected) == {str(job.id) for job in [importing, child, orphan_job]}
    assert all(
        job.status == "failed" and job.error == "Connection disabled"
        for job in [importing, child, orphan_job]
    )
    assert shared_job.status == "queued" and shared_job.error is None
    assert (saved.enabled, saved.revision, saved.sync_revision) == (False, 4, 3)


@pytest.mark.parametrize("case", ["enable", "explicit_sync", "already_paused", "unchanged"])
def test_connection_controls_only_start_work_for_explicit_sync_or_reenable(monkeypatch, case):
    saved = connection(enabled=case != "enable" and case != "already_paused")
    session = Mock(spec=Session)
    queued = work(saved)
    request = Mock(return_value=queued)
    monkeypatch.setattr(connections, "request_sync", request)
    enabled = case != "already_paused"
    affected = connections.configure_connection(
        session, saved, enabled, sync=case == "explicit_sync"
    )
    if case in {"enable", "explicit_sync"}:
        assert affected == [str(queued.id)]
        request.assert_called_once_with(session, saved)
    else:
        assert affected == []
        request.assert_not_called()
        session.scalars.assert_not_called()


@pytest.mark.parametrize(
    "last,expected",
    [
        (NOW - timedelta(minutes=30), NOW + timedelta(minutes=30)),
        (NOW - timedelta(hours=2), NOW),
        (None, NOW + timedelta(hours=1)),
    ],
)
def test_interval_edits_recompute_due_time_without_invalidating_generation(last, expected):
    saved = connection(last_sync_at=last)
    session = Mock(spec=Session)
    assert connections.configure_connection(session, saved, True, sync_interval_minutes=60) == []
    assert (saved.next_sync_at, saved.revision, saved.sync_revision) == (expected, 4, 2)
    session.scalars.assert_not_called()


@pytest.mark.parametrize("changed", ["none", "credentials", "endpoint"])
def test_definition_changes_fence_active_generation_and_increment_both_revisions(changed):
    saved = connection()
    definition = ConnectorConfig.model_validate(connector_snapshot(saved))
    if changed == "endpoint":
        definition = definition.model_copy(update={"list_path": "/api/v2/products/"})
    running = work(saved, status="running")
    session = Mock(spec=Session)
    session.scalars.return_value = [running]
    assert connections.update_connection_definition(
        session, saved, definition, credential_changed=changed == "credentials"
    ) is (changed != "none")
    if changed == "none":
        assert (saved.revision, saved.sync_revision, running.status) == (3, 2, "running")
        session.flush.assert_not_called()
    else:
        assert (saved.revision, saved.sync_revision, running.status) == (4, 3, "failed")
        assert running.finished_at == NOW and running.lease_until is None
        assert saved.connector == definition.model_dump(mode="json")


@pytest.mark.parametrize(
    "case", ["unqualified", "no_source", "active", "empty", "same_snapshot", "new_snapshot"]
)
def test_evaluation_admission_samples_current_facts_and_coalesces_same_snapshot(monkeypatch, case):
    value, story = product(), article()
    if case == "unqualified":
        value.status = "pending"
    session = Mock(spec=Session)
    monkeypatch.setattr(connections, "product_has_active_source", lambda *_: case != "no_source")
    snapshot = {
        "version": "1",
        "product": product_snapshot(value),
        "articles": [article_snapshot(story)],
    }
    latest = PartnerEvaluation(
        id=uuid.uuid4(),
        product_id=value.id,
        status="succeeded",
        snapshot=snapshot,
        finished_at=NOW - timedelta(minutes=1),
        result={"decisions": []},
    )
    session.scalar.side_effect = [
        uuid.uuid4() if case == "active" else None,
        latest if case == "same_snapshot" else None,
    ]
    session.execute.side_effect = [result([] if case == "empty" else [story]), result([])]
    connections.queue_evaluation(session, value)
    if case == "new_snapshot":
        queued = session.add.call_args.args[0]
        assert isinstance(queued, PartnerEvaluation) and queued.snapshot == snapshot
        assert queued.requested_by == connections.SYSTEM_ACTOR
    else:
        session.add.assert_not_called()


@pytest.mark.parametrize(
    "enabled,syncing,error,state",
    [
        (False, False, None, "disconnected"),
        (True, True, "Old failure", "syncing"),
        (True, False, "API unavailable", "error"),
        (True, False, None, "idle"),
    ],
)
def test_connection_views_expose_operator_state_and_attention_without_credential_material(
    enabled, syncing, error, state
):
    saved = connection(enabled=enabled, last_error=error)
    session = Mock(spec=Session)
    session.execute.return_value = [
        NS(
            provider=saved.provider,
            approval_mode="qualified",
            account_tier="silver",
            syncing=syncing,
            products=7,
            qualified=3,
            checking=2,
            pending=3,
            excluded=1,
            commercial_approved=3,
        )
    ]
    output = connections.connection_view(session, saved)
    assert (output.state, output.needs_attention, output.products) == (state, 1, 7)
    assert output.next_sync_at == (NOW if enabled else None)
    assert output.credentials_available is False and output.credential_configured is False
    assert output.api_url == "https://nicklaunches.com/api/v1/products/"
    assert "encrypted_credential" not in output.model_dump()


def test_empty_connection_page_needs_no_aggregate_read():
    session = Mock(spec=Session)
    assert connections.connection_views(session, []) == []
    session.execute.assert_not_called()


def test_downgrade_applies_supported_interval_to_current_schedule():
    saved = connection(account_id=uuid.uuid4(), sync_interval_minutes=60)
    session = Mock(spec=Session)
    session.get.return_value = PartnerAccount(
        id=saved.account_id, name="Launch platform", tier="bronze", status="active"
    )
    assert connections.connection_sync_interval(session, saved) == 1440
